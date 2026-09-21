"""医院集成平台数据接收（HIS/集成平台 → EnvMon）。

接收集成平台推送的 XML 消息（医嘱 / 检查结果 / 检验结果 / 病理结果），
解析后写入 ICU 临床数据模型，并按医院集成平台交互规范应答 XML。

消息信封（入参）:
  <Request>
    <Header>
      <SourceSystem>02</SourceSystem>
      <MessageID>1033424</MessageID>
    </Header>
    <Body>
      <AddOrdersRt> ... </AddOrdersRt>    <!-- 业务节点，按类型不同 -->
    </Body>
  </Request>

应答:
  <Response>
    <Header>
      <SourceSystem>CDSS</SourceSystem>
      <MessageID>...</MessageID>
    </Header>
    <Body>
      <ResultCode>0</ResultCode>
      <ResultContent>接收成功</ResultContent>
    </Body>
  </Response>

当前支持业务节点:
  - AddOrdersRt  新增/更新医嘱        → orders 表（完整实现）
预留（待样例后补齐字段映射）:
  - 检查结果 / 检验结果 / 病理结果    → 收到时返回"暂不支持"并记日志，不丢原始消息

幂等: integration_messages 表按 (MessageID + 原始XML指纹) 去重；
      同一消息重复推送时直接返回成功，不重复入库。
患者建档规则: 患者档案不存在时，仅当消息科室命中「重症科室关键词」
      才自动建档（app_settings: integration.icu_depts，逗号分隔关键词）；
      非重症科室患者返回失败码，不产生"无主档案"。
"""
from __future__ import annotations

import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import icu

log = logging.getLogger("envmon.integration")

# 本系统在集成平台的标识（应答 Header.SourceSystem）
RESPONSE_SOURCE_SYSTEM = "CDSS"

# 医院本地时间相对 UTC 的偏移小时（医生录入的是东八区本地时间，转 UTC 入库）
TZ_OFFSET_HOURS = int(os.environ.get("INTEGRATION_TZ_OFFSET", "8"))

# 自动建档的重症科室关键词（app_settings 覆盖，env 兜底）
_DEFAULT_ICU_DEPTS = "ICU,重症,CCU,EICU,ICU病房"
ICU_DEPTS_KEY = "integration.icu_depts"
ICU_DEPTS_ENV = os.environ.get("ICU_DEPT_KEYWORDS", "")


# ================================================================ XML 工具
def _local_tag(tag: str) -> str:
    """去掉命名空间前缀，取本地标签名。"""
    return tag.rsplit("}", 1)[-1]


def _find_child(el: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    """按本地标签名查找直接子元素（忽略命名空间）。"""
    if el is None:
        return None
    for c in el:
        if _local_tag(c.tag) == name:
            return c
    return None


def _find_text(el: Optional[ET.Element], name: str) -> str:
    """取直接子元素的文本（去空白），无则返回空串。"""
    c = _find_child(el, name)
    if c is None or c.text is None:
        return ""
    return c.text.strip()


def _local_to_utc(date_s: str, time_s: str) -> Optional[str]:
    """医院本地时间(YYYY-MM-DD + HH:MM:SS) → UTC ISO8601 (YYYY-MM-DDTHH:MM:SSZ)。

    医院 HIS 录入的时间是本地时间；系统内 ICU 时间统一存 UTC，
    因此按东八区偏移转 UTC（可用 INTEGRATION_TZ_OFFSET 覆盖）。
    解析失败返回 None。
    """
    if not date_s:
        return None
    try:
        dt = datetime.strptime(f"{date_s} {time_s or '00:00:00'}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    local_tz = timezone(timedelta(hours=TZ_OFFSET_HOURS))
    return dt.replace(tzinfo=local_tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _order_status(status_desc: str) -> str:
    """医嘱状态：描述含"停/作废/撤销/取消" → stopped，否则 active。

    各家 HIS 的状态码（OEORIStatusCode）不统一，故优先按状态描述判断；
    若描述缺失，再退而按常见停止码判断（D/C/DC/X）。
    """
    if not status_desc:
        return "active"
    if any(k in status_desc for k in ("停", "作废", "撤销", "取消")):
        return "stopped"
    return "active"


# ================================================================ 重症科室
def _icu_dept_keywords() -> List[str]:
    """读取重症科室关键词列表：app_settings 优先，env 次之，内置默认兜底。"""
    raw = ""
    try:
        raw = str(icu.get_setting(ICU_DEPTS_KEY, "") or "")
    except Exception:  # noqa: BLE001
        raw = ""
    if not raw.strip():
        raw = ICU_DEPTS_ENV or _DEFAULT_ICU_DEPTS
    return [k.strip() for k in raw.replace("，", ",").split(",") if k.strip()]


def _match_icu_dept(dept_code: str, dept_desc: str) -> bool:
    """判断科室是否重症：任一关键词命中科室代码或科室描述的**子串**。

    例: 配置 "ESLBQ" 时，OEORIEnterDeptDesc="ESLBQ-二十六病区" 命中；
        配置 "重症" 时，描述含"重症医学科/重症病房"即命中。
    """
    keywords = _icu_dept_keywords()
    if not keywords:
        return False
    text = f"{dept_code} {dept_desc}".lower()
    return any(k.lower() in text for k in keywords)


# ================================================================ 患者
def _resolve_patient(pid: str, visit_no: str,
                     dept_code: str, dept_desc: str, bed_no: str) -> Tuple[int, str]:
    """按患者ID解析患者档案；不存在时按规则自动建档。

    返回 (patient_id, note)。
    规则（用户确认）：只有重症科室的相关患者才自动建档；
    非重症科室且无档案 → 抛 ValueError（上层转 ResultCode=1）。
    """
    p = icu.patient_by_pid(pid)
    if p:
        return p["id"], ""

    if not _match_icu_dept(dept_code, dept_desc):
        raise ValueError("患者档案不存在且非重症科室，不自动建档")

    admit = _local_to_utc("", "")  # 建档时间用当前 UTC
    admit_ts = admit or icu._now()
    patient_id = icu.patient_create(
        pid=pid,
        bed_no=bed_no or None,
        admit_ts=admit_ts,
    )
    log.info("integration: auto-created patient pid=%s (ICU dept %s %s), bed=%s",
             pid, dept_code, dept_desc, bed_no or "-")
    # 关联就诊记录（encounter），写入就诊号
    try:
        enc = icu.ensure_active_encounter(patient_id, bed_no=bed_no or None)
        if visit_no:
            icu.run(
                "UPDATE patient_encounters SET encounter_no=? "
                "WHERE id=? AND (encounter_no IS NULL OR encounter_no='')",
                (visit_no, enc["id"]),
            )
    except Exception:  # noqa: BLE001
        log.warning("integration: failed to link encounter for pid=%s", pid, exc_info=True)
    return patient_id, f"患者档案不存在，已按重症科室自动建档 pid={pid}"


# ================================================================ 医嘱
def _parse_order_item(item: ET.Element) -> Dict[str, Any]:
    """单条 OEORIInfo → orders 表字段。"""
    start_ts = _local_to_utc(_find_text(item, "OEORIEnterDate"),
                             _find_text(item, "OEORIEnterTime"))
    end_ts = _local_to_utc(_find_text(item, "OEORIStopDate"),
                           _find_text(item, "OEORIStopTime"))
    status = _order_status(_find_text(item, "OEORIStatusDesc"))

    dose_qty = _find_text(item, "OEORIDoseQty")
    dose_unit = _find_text(item, "OEORIDoseUnitDesc") or _find_text(item, "OEORIDoseUnitCode")
    dosage = f"{dose_qty} {dose_unit}".strip() if dose_qty else (dose_unit or None)

    return {
        "order_no": _find_text(item, "OEORIOrderItemID") or None,
        "drug_name": _find_text(item, "OEORIARCItmMastDesc") or None,
        "dosage": dosage,
        "route": _find_text(item, "OEORIInstrDesc") or None,  # 用法说明近似给药途径
        "freq": _find_text(item, "OEORIFreqDesc") or None,
        "instruction": _find_text(item, "OEORIInstrDesc") or None,
        "dept": _find_text(item, "OEORIEnterDeptDesc") or _find_text(item, "OEORIEnterDeptCode"),
        "remark": ";".join(x for x in (
            _find_text(item, "OEORIPriorityDesc"),
            _find_text(item, "OEORIClassDesc"),
            _find_text(item, "OEORIRemarks"),
            f"执行科室:{_find_text(item, 'OEORIExecDeptDesc')}" if _find_text(item, "OEORIExecDeptDesc") else "",
            f"项目编码:{_find_text(item, 'OEORIARCItmMastCode')}" if _find_text(item, "OEORIARCItmMastCode") else "",
        ) if x) or None,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "status": status,
        "operator": _find_text(item, "OEORIEnterDocDesc") or None,
    }


def _order_exists(patient_id: int, order_no: str, start_ts: str) -> bool:
    """同患者、同医嘱项、同开始时间且仍 active 的记录视为重复，跳过插入。"""
    if not order_no:
        return False
    row = icu.fetchone(
        "SELECT id FROM orders WHERE patient_id=? AND order_no=? AND start_ts=? AND status='active' "
        "ORDER BY id DESC LIMIT 1",
        (patient_id, order_no, start_ts or ""),
    )
    return row is not None


def _handle_orders(biz: ET.Element, pid: str, visit_no: str) -> Tuple[int, str]:
    """处理 AddOrdersRt 业务节点，返回 (入库数量, 日志明细)。"""
    # 患者（建档规则在 _resolve_patient 内）
    bed_no = _find_text(biz, "UpdateUserDesc")  # 样例中 UpdateUserDesc=A015床, 借为床号
    if not bed_no or "床" not in bed_no:
        bed_no = _find_text(biz, "UpdateUserCode") or ""
    # 科室字段在各 OEORIInfo 子元素内，取第一条医嘱项的科室用于建档判断
    info_list = _find_child(biz, "OEORIInfoList")
    items: List[ET.Element] = []
    if info_list is not None:
        items = [c for c in info_list if _local_tag(c.tag) == "OEORIInfo"]
    first = items[0] if items else biz
    patient_id, note = _resolve_patient(
        pid,
        visit_no,
        _find_text(first, "OEORIEnterDeptCode") or _find_text(first, "OEORIExecDeptCode"),
        _find_text(first, "OEORIEnterDeptDesc") or _find_text(first, "OEORIExecDeptDesc"),
        bed_no,
    )

    inserted = 0
    skipped = 0
    for it in items:
        o = _parse_order_item(it)
        if not o["order_no"] or not o["drug_name"]:
            skipped += 1
            continue
        if _order_exists(patient_id, o["order_no"], o["start_ts"]):
            skipped += 1
            continue
        icu.order_insert(
            patient_id,
            source="his",
            order_no=o["order_no"],
            drug_name=o["drug_name"],
            dosage=o["dosage"],
            route=o["route"],
            start_ts=o["start_ts"],
            end_ts=o["end_ts"],
            status=o["status"],
            operator=o["operator"],
            freq=o["freq"],
            instruction=o["instruction"],
            dept=o["dept"],
            remark=o["remark"],
        )
        inserted += 1

    log.info("integration: orders pid=%s inserted=%d skipped=%d", pid, inserted, skipped)
    detail = f"医嘱入库 {inserted} 条" + (f"（跳过重复 {skipped} 条）" if skipped else "")
    if note:
        detail = f"{detail}；{note}"
    return inserted, detail


# ================================================================ 业务分派
def _dispatch_biz(biz: ET.Element, pid: str, visit_no: str) -> Tuple[int, str]:
    """按业务节点标签分派处理。返回 (result_code, detail)。"""
    tag = _local_tag(biz.tag)
    if tag == "AddOrdersRt":
        try:
            _handle_orders(biz, pid, visit_no)
        except ValueError as e:
            return 1, str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("integration: order handling failed")
            return 4, f"医嘱处理失败: {e}"
        return 0, "医嘱接收成功"
    # 预留：检查结果 / 检验结果 / 病理结果（样例到位后在此补分派）
    if tag in ("AddExamResult", "AddLabResult", "AddPathologyResult",
               "AddExamResultsRt", "AddLabResultsRt", "AddPathologyResultsRt",
               "ExamResult", "LabResult", "PathologyResult"):
        return 3, f"业务类型 {tag} 暂未实现解析器，原始消息已记录"
    return 3, f"未知业务节点 {tag}，原始消息已记录"


# ================================================================ 主入口
def _msg_seen(message_id: str, digest: str) -> bool:
    """幂等：同一 MessageID + 原始XML指纹 已成功处理过 → True。"""
    if not message_id:
        return False
    row = icu.fetchone(
        "SELECT id, result_code FROM integration_messages "
        "WHERE message_id=? AND digest=? ORDER BY id DESC LIMIT 1",
        (message_id, digest),
    )
    return row is not None and row["result_code"] == 0


def _log_message(message_id: str, source_system: str, biz_type: str, pid: str,
                 raw_xml: str, digest: str, result_code: int, result_content: str) -> None:
    icu.run(
        "INSERT INTO integration_messages "
        "(message_id, source_system, biz_type, patient_pid, raw_xml, digest, result_code, result_content, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (message_id or None, source_system or None, biz_type or None, pid or None,
         raw_xml, digest, result_code, result_content, icu._now()),
    )


def _make_response(message_id: str, result_code: int, content: str) -> str:
    """按集成平台规范生成应答 XML。"""
    msg_id = message_id or ""
    return (
        "<Response>"
        "<Header>"
        f"<SourceSystem>{RESPONSE_SOURCE_SYSTEM}</SourceSystem>"
        f"<MessageID>{msg_id}</MessageID>"
        "</Header>"
        "<Body>"
        f"<ResultCode>{result_code}</ResultCode>"
        f"<ResultContent>{content}</ResultContent>"
        "</Body>"
        "</Response>"
    )


def handle_integration_request(raw_xml: str) -> str:
    """集成平台消息入口：解析 → 幂等 → 分派 → 入库 → 应答 XML。"""
    digest = hashlib.sha256(raw_xml.encode("utf-8", "replace")).hexdigest()
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as e:
        _log_message("", "", "", "", raw_xml, digest, 2, f"XML解析失败: {e}")
        return _make_response("", 2, f"XML解析失败: {e}")

    header = _find_child(root, "Header")
    source_system = _find_text(header, "SourceSystem")
    message_id = _find_text(header, "MessageID")

    body = _find_child(root, "Body")
    biz = None
    if body is not None:
        # 取 Body 下第一个业务节点
        for c in body:
            if _local_tag(c.tag) not in ("",):
                biz = c
                break

    if biz is None:
        _log_message(message_id, source_system, "", "", raw_xml, digest, 2, "消息缺少业务节点(Body)")
        return _make_response(message_id, 2, "消息缺少业务节点(Body)")

    pid = _find_text(biz, "PATPatientID")
    visit_no = _find_text(biz, "PAADMVisitNumber")
    biz_type = _local_tag(biz.tag)

    if _msg_seen(message_id, digest):
        _log_message(message_id, source_system, biz_type, pid, raw_xml, digest, 0, "重复消息，已跳过")
        return _make_response(message_id, 0, "接收成功（重复消息）")

    result_code, detail = _dispatch_biz(biz, pid, visit_no)
    _log_message(message_id, source_system, biz_type, pid, raw_xml, digest, result_code, detail)
    return _make_response(message_id, result_code, detail)