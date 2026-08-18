# 06 · API 接口文档 (v2.2)

Base URL: `http://<host>:12090`
认证: `Authorization: Bearer <token>` (POST /api/login 除外)
Content-Type: `application/json` (除 OTA 上传为 `multipart/form-data`)

---

## 一、认证与用户

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 登录，返回 token + 用户信息 |
| GET | `/api/me` | 当前用户 |
| PUT | `/api/me/password` | 改密 `{"old_password","new_password"}` |
| PUT | `/api/me/sound` | 报警声音开关 `{"sound_alarm":bool}` |
| POST | `/api/logout` | 登出 |
| GET | `/api/users` | 用户列表 (admin) |
| POST | `/api/users` | 新建用户 (admin) |
| DELETE | `/api/users/{id}` | 删除用户 (admin) |

登录请求体: `{"username":"admin","password":"***"}`
响应: `{"token":"...","user":{...}}`

---

## 二、设备与环境

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/devices` | 设备列表 |
| POST | `/api/devices` | 注册设备 `{"device_id","name"}` |
| GET | `/api/devices/{id}` | 设备详情 |
| PUT | `/api/devices/{id}` | 更新设备 |
| DELETE | `/api/devices/{id}` | 删除 |
| GET | `/api/realtime` | 各设备最新值 |
| GET | `/api/history` | 历史曲线 |
| POST | `/api/ingest` | 接收设备上报 payload |
| GET | `/api/thresholds` | 读取阈值 |
| PUT | `/api/thresholds` | 更新阈值并下发 |
| GET | `/api/alarms` | 报警事件列表 |

history 参数: `?device=<id>&hours=<n>&fields=<csv>`，fields 可选: `temp_c,hum_pct,pres_hpa,sp_o2,pr_hr,ecg_hr,sbp,dbp,temp_c,glucose`

阈值 body 示例:
```json
{"device_id":"...","temp_min":20,"temp_max":40,"hum_min":30,"hum_max":80,"pres_min":950,"pres_max":1080,"report_interval":30,"alarm_enabled":true,"alarm_sound":true}
```

---

## 三、OTA 远程升级

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ota/version` | 当前最新版本号 |
| GET | `/api/ota/image` | 下载最新 bin (无 token 也允许) |
| GET | `/api/ota/list` | 版本列表 |
| POST | `/api/ota/upload` | 上传 bin `multipart file+version` |
| POST | `/api/ota/push/{device_id}` | 向设备推送升级指令 |
| DELETE | `/api/ota/{id}` | 删除版本 |

上传 body: `multipart/form-data`, 字段 `file` (.bin) 和 `version` (如 `v1.8.0`)。
响应: `{"version":"v1.8.0","sha256":"...","size":871360,"is_latest":true}`

---

## 四、患者 (ICU v2.1+)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/patients` | 患者列表 |
| POST | `/api/patients` | 新建患者 |
| GET | `/api/patients/{pid}` | 详情 |
| PUT | `/api/patients/{pid}` | 更新 |
| DELETE | `/api/patients/{pid}` | 删除 (级联) |
| POST | `/api/patients/{pid}/link/{device_id}` | 关联设备 |
| DELETE | `/api/patients/{pid}/unlink/{device_id}` | 解绑 |
| GET | `/api/patients/{pid}/devices` | 患者设备列表 |

患者字段: `pid,name,gender(M/F),age,bed_no,diagnosis,doctor,phone,admit_ts,created_at,updated_at`

---

## 五、体征 Vitals

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/patients/{pid}/vitals` | 录入体征 |
| GET | `/api/patients/{pid}/vitals` | 拉取曲线 |

vitals 字段 (均可选，null 则不存):
`source,ts,sp_o2,pr_hr,ecg_hr,ecg_st,rr_bpm,etco2,sbp,dbp,map_bp,ibp,temp_c,glucose,hum_pct,pres_hpa,k_mmol,na_mmol,cl_mmol,ca_mmol,glucose_lab,lactate,ph,pco2,po2,hco3,be,alarm_flag,alarm_reason,extra`

get 参数: `?hours=<n>&fields=<csv>&start=<iso>&end=<iso>`
返回: `{"count":N,"points":[...]}` (按 ts 升序)

---

## 六、医嘱 Orders

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/patients/{pid}/orders` | 开医嘱 |
| GET | `/api/patients/{pid}/orders` | 医嘱列表 |
| POST | `/api/patients/{pid}/orders/{id}/stop` | 停医嘱 |

字段: `source,order_no,drug_name,dosage,route,rate_mlph,start_ts,end_ts,operator,status`
route 枚举: `ivgtt,pump,iv,im,po,ih,nebul,enema`

---

## 七、LIS 检验

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/patients/{pid}/lab` | 录入检验 |
| GET | `/api/patients/{pid}/lab` | 检验列表 |

字段: `source(lis/blood_gas/manual),item_code,item_name,value,unit,ref_min,ref_max,result_ts`
自动计算: `critical` (超出 ref 范围则标 1)

---

## 八、出入量 IO (v2.2)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/patients/{pid}/io` | 录入出入量 |
| GET | `/api/patients/{pid}/io` | 条目列表 |
| GET | `/api/patients/{pid}/io/balance` | 净平衡 |

录入字段:
- `direction`: `in` / `out` (必填)
- `kind`: `fluid/urine/drain/stool/emesis/suction/other` (必填)
- `sub_type`: 具体类别如 `生理盐水/白蛋白/血/胃液/痰/腹腔引流`
- `amount_ml`: 体积 (mL)
- `amount_g`: 重量 (g)
- `route`: `iv/po/drain/np/urine/rectum`
- `note,source,operator,ts,unique_id`: 可选

balance 参数: `?hours=<n>` (默认 24)
返回: `{"in_ml":1275,"out_ml":950,"net_ml":325,"hours":24}`
（`amount_ml` + `amount_g` 合并计入入或出，用于统一单位折算）

---

## 九、AI 评估 (v2.2)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/patients/{pid}/assessment` | 规则式 AI 评估 |

参数: `?hours=<n>` (默认 24)

返回结构:
```json
{
  "pid": "PACU-001",
  "patient_name": "张三",
  "assessed_at": "2026-08-18T10:30:00Z",
  "overall_risk": "critical",          // low / moderate / high / critical
  "risk_score": 3,                     // 0 / 1 / 2 / 3
  "crit_count": 4,                     // LV2 系统数
  "warn_count": 6,                     // LV1+ 系统数
  "vitals_count": 24,
  "orders_active": 3,
  "labs_recent": 7,
  "io_balance": {"in_ml":1275,"out_ml":950,"net_ml":325,"hours":24},
  "systems": {
    "cardiac":     {"label":"循环系统","hr":131,"pr_hr":131,"sbp":125,"dbp":80,"trend":"↗","risk":2,"note":"..."},
    "respiratory": {"label":"呼吸系统","rr":28,"sp_o2":87.5,"trend":"↗","risk":2,"note":"血氧<90%，高流量吸氧…"},
    "neuro":       {"label":"神经系统","gcs":null,"trend":"➡","risk":0,"note":"GCS 未接入，请人工评估"},
    "endo":        {"label":"内分泌","glucose":5.5,"trend":"➡","risk":0,"note":null},
    "renal":       {"label":"肾功能","creatinine":150,"urine_hr_ml":37.5,"trend":"➡","risk":2,"note":"肌酐显著升高，评估 AKI"},
    "heme":        {"label":"血液/凝血","hgb":68,"plt":75,"trend":"➡","risk":2,"note":"血红蛋白 <60g/L，评估输血"},
    "acid_base":   {"label":"酸碱/代谢","ph":7.28,"lactate":5.0,"trend":"➡","risk":2,"note":"乳酸>4，组织灌注不足，启动 sepsis 处理"},
    "fluid":       {"label":"液体平衡","in_ml":1275,"out_ml":950,"net_ml":325,"hours":24,"trend":"↗","risk":1,"note":"正平衡显著…"}
  },
  "summary": "PACU-001（张三，M，68岁，ICU-A1）诊断「急性心梗」…",
  "actions": ["关注：循环系统：心率异常…","建议立即查房评估…","考虑调整补液策略…"],
  "disclaimer": "AI 评估基于规则引擎，仅供参考，不构成诊疗建议。临床决策须由主治医师负责。"
}
```

---

## 十、备份

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/backup` | 触发备份 (admin) |
| GET | `/api/backup` | 备份列表 (admin) |

触发返回: `{"path":..., "size":..., "sha256":..., "ok":true}`

---

## WebSocket

`ws://<host>:12090/ws?token=<token>`

服务端推送消息:
- `{"type":"telemetry","device_id":"...","data":{...}}` — 设备上报
- `{"type":"status","device_id":"...","online":true}` — 设备上下线
- `{"type":"alarm","device_id":"...","level":2,"reason":"..."}` — 报警
- `{"type":"alarm_cleared","device_id":"..."}` — 消警
- `{"type":"vital","pid":"...","ts":"...","source":"..."}` — 新体征
- `{"type":"order","pid":"...","order_id":N}` — 新医嘱
- `{"type":"lab","pid":"...","lab_id":N}` — 新检验

---

## 权限矩阵

| 端点 | 未登录 | viewer | admin |
|------|--------|--------|-------|
| /api/login, /api/ota/image, /api/health | ✓ | ✓ | ✓ |
| /api/devices (GET), /api/realtime, /api/history | ✗ | ✓ | ✓ |
| /api/patients, /api/patients/* | ✗ | ✓ | ✓ |
| /api/patients/*/vitals, /orders, /lab, /io (POST) | ✗ | ✗ | ✓ |
| /api/thresholds (PUT), /api/devices (POST/PUT/DELETE) | ✗ | ✗ | ✓ |
| /api/backup, /api/users, /api/ota/upload | ✗ | ✗ | ✓ |