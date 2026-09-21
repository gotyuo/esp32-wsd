"""integration 集成平台接收单元测试。

覆盖:
  - 医嘱消息（AddOrdersRt）: 重症科室患者自动建档 + 医嘱入库
  - 非重症科室患者: 不建档, 返回失败码 1
  - 已有患者: 直接关联, 不重复建档
  - 幂等: 同一 MessageID 重复推送跳过
  - integration_messages 日志落库
说明: 使用临时 DB（DB_PATH 指向 tmp 文件，import 前设置）。
"""
import io
import os
import sys
import tempfile
import unittest

# 临时 DB 必须在本模块 import db/icu 之前设置（模块级读取 DB_PATH）
_tmpdir = tempfile.mkdtemp(prefix="envmon_integration_test_")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "test.db")
os.environ["SCHEMA_FILE"] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema.sql")

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER_DIR)  # 以包方式导入 app.*，支持 integration 的相对导入

from app.db import init_db  # noqa: E402
from app.integration import handle_integration_request  # noqa: E402
import app.icu as icu  # noqa: E402

ORDER_XML_ICU = """<Request><Header><SourceSystem>02</SourceSystem><MessageID>1033424</MessageID></Header><Body><AddOrdersRt><BusinessFieldCode>00002</BusinessFieldCode><HospitalCode>HBSZYY</HospitalCode><PATPatientID>ICU-P-0001</PATPatientID><PAADMVisitNumber>19204512</PAADMVisitNumber><PAAdmTypeCode>I</PAAdmTypeCode><OEORIInfoList><OEORIInfo><BusinessFieldCode>00002</BusinessFieldCode><HospitalCode>HBSZYY</HospitalCode><OEORIOrderItemID>17770682||171</OEORIOrderItemID><OEORIARCItmMastCode>YL000073</OEORIARCItmMastCode><OEORIARCItmMastDesc>快速血糖</OEORIARCItmMastDesc><OEORIPriorityCode>NORM</OEORIPriorityCode><OEORIPriorityDesc>临时医嘱</OEORIPriorityDesc><OEORIStatusCode>V</OEORIStatusCode><OEORIStatusDesc>核实</OEORIStatusDesc><OEORIClass>25</OEORIClass><OEORIClassDesc>治疗</OEORIClassDesc><OEORIDoseQty>1</OEORIDoseQty><OEORIDoseUnitDesc>每试验</OEORIDoseUnitDesc><OEORIFreqDesc>Q6H</OEORIFreqDesc><OEORIRemarks></OEORIRemarks><OEORIEnterDocCode>60713</OEORIEnterDocCode><OEORIEnterDocDesc>王晓倩</OEORIEnterDocDesc><OEORIEnterDate>2026-09-20</OEORIEnterDate><OEORIEnterTime>17:08:51</OEORIEnterTime><OEORIEnterDeptCode>ICU</OEORIEnterDeptCode><OEORIEnterDeptDesc>ICU-重症医学科</OEORIEnterDeptDesc><OEORIExecDeptCode>ICU</OEORIExecDeptCode><OEORIExecDeptDesc>ICU-重症医学科</OEORIExecDeptDesc><OEORIRequireExecDate>2026-09-20</OEORIRequireExecDate><OEORIRequireExecTime>17:08:51</OEORIRequireExecTime><OEORIParentOrderID>17770682||171</OEORIParentOrderID><OEORIPrice>3</OEORIPrice><OEORIBilled>未收费</OEORIBilled></OEORIInfo></OEORIInfoList><UpdateUserCode>ICU</UpdateUserCode><UpdateUserDesc>A015床</UpdateUserDesc><UpdateDate>2026-09-20</UpdateDate><UpdateTime>17:09:02</UpdateTime></AddOrdersRt></Body></Request>"""

ORDER_XML_WARD = ORDER_XML_ICU.replace("ICU-P-0001", "WARD-P-0001").replace(
    "<OEORIEnterDeptCode>ICU</OEORIEnterDeptCode>", "<OEORIEnterDeptCode>26BQ</OEORIEnterDeptCode>"
).replace(
    "<OEORIEnterDeptDesc>ICU-重症医学科</OEORIEnterDeptDesc>", "<OEORIEnterDeptDesc>二十六病区</OEORIEnterDeptDesc>"
).replace(
    "<OEORIExecDeptCode>ICU</OEORIExecDeptCode>", "<OEORIExecDeptCode>26BQ</OEORIExecDeptCode>"
).replace(
    "<OEORIExecDeptDesc>ICU-重症医学科</OEORIExecDeptDesc>", "<OEORIExecDeptDesc>二十六病区</OEORIExecDeptDesc>"
).replace("<UpdateUserCode>ICU</UpdateUserCode>", "<UpdateUserCode>26BQ</UpdateUserCode>")

UNKNOWN_XML = ORDER_XML_ICU.replace("<AddOrdersRt>", "<UnknownBiz>").replace("</AddOrdersRt>", "</UnknownBiz>")


class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def tearDown(self):
        # 每测后清空业务表，保持用例独立
        for tbl in ("integration_messages", "orders", "patient_encounters", "patients"):
            icu.run(f"DELETE FROM {tbl}")

    def test_icu_patient_auto_created_and_order_inserted(self):
        resp = handle_integration_request(ORDER_XML_ICU)
        self.assertIn("<ResultCode>0</ResultCode>", resp)
        self.assertIn("接收成功", resp)

        p = icu.patient_by_pid("ICU-P-0001")
        self.assertIsNotNone(p, "重症科室患者应自动建档")
        self.assertEqual(p["bed_no"], "A015床")

        orders = icu.orders_for_patient(p["id"])
        self.assertEqual(len(orders), 1)
        o = orders[0]
        self.assertEqual(o["order_no"], "17770682||171")
        self.assertEqual(o["drug_name"], "快速血糖")
        self.assertEqual(o["dosage"], "1 每试验")
        self.assertEqual(o["freq"], "Q6H")
        self.assertEqual(o["dept"], "ICU-重症医学科")
        self.assertEqual(o["status"], "active")
        self.assertEqual(o["operator"], "王晓倩")
        # 17:08:51 +08:00 -> 09:08:51Z
        self.assertEqual(o["start_ts"], "2026-09-20T09:08:51Z")

    def test_non_icu_patient_not_created(self):
        resp = handle_integration_request(ORDER_XML_WARD)
        self.assertIn("<ResultCode>1</ResultCode>", resp)
        self.assertIn("非重症科室", resp)
        self.assertIsNone(icu.patient_by_pid("WARD-P-0001"), "非重症科室患者不应建档")

    def test_existing_patient_reused(self):
        icu.patient_create("EXIST-1", name="已有患者")
        xml = ORDER_XML_ICU.replace("ICU-P-0001", "EXIST-1").replace(
            "<OEORIEnterDeptCode>ICU</OEORIEnterDeptCode>", "<OEORIEnterDeptCode>26BQ</OEORIEnterDeptCode>"
        ).replace(
            "<OEORIEnterDeptDesc>ICU-重症医学科</OEORIEnterDeptDesc>", "<OEORIEnterDeptDesc>二十六病区</OEORIEnterDeptDesc>"
        ).replace(
            "<OEORIEnterDeptCode>ICU</OEORIEnterDeptCode>", "<OEORIEnterDeptCode>26BQ</OEORIEnterDeptCode>"
        )
        resp = handle_integration_request(xml)
        self.assertIn("<ResultCode>0</ResultCode>", resp)
        p = icu.patient_by_pid("EXIST-1")
        self.assertEqual(p["name"], "已有患者", "已有档案应直接复用，不覆盖姓名")

    def test_idempotent_same_message_id(self):
        r1 = handle_integration_request(ORDER_XML_ICU)
        self.assertIn("<ResultCode>0</ResultCode>", r1)
        r2 = handle_integration_request(ORDER_XML_ICU)
        self.assertIn("<ResultCode>0</ResultCode>", r2)
        p = icu.patient_by_pid("ICU-P-0001")
        self.assertEqual(len(icu.orders_for_patient(p["id"])), 1, "重复消息不应重复入库")

    def test_unknown_biz_recorded(self):
        resp = handle_integration_request(UNKNOWN_XML)
        self.assertIn("<ResultCode>3</ResultCode>", resp)
        row = icu.fetchone(
            "SELECT * FROM integration_messages WHERE biz_type='UnknownBiz' ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(row)
        self.assertIn("UnknownBiz", row["result_content"])

    def test_message_log_written(self):
        handle_integration_request(ORDER_XML_ICU)
        row = icu.fetchone(
            "SELECT * FROM integration_messages WHERE message_id='1033424' ORDER BY id DESC LIMIT 1")
        self.assertIsNotNone(row)
        self.assertEqual(row["source_system"], "02")
        self.assertEqual(row["result_code"], 0)
        self.assertIn("<Request>", row["raw_xml"])


if __name__ == "__main__":
    unittest.main(verbosity=2)