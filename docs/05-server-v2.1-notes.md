# 服务器端优化说明 v2.1

## 修复项

### 1-7. 全部刷新按钮无反应

**根因**: 前端 9 个刷新按钮的 `onclick` 属性多了一个引号(`onclick="loadXxx(event)""`),导致 JS 语法错误,点击无任何反应。同时 `_refreshBtn()` 使用全局定时器 `_rbTimer` 锁,一旦某次刷新超时 8 秒,所有刷新按钮在此期间全部失效。

**修复**:
- 修正全部 9 处 onclick 多余引号
- 重写 `_refreshBtn()`: 改为仅锁定当前按钮(不用全局锁),使用 `currentTarget` 替代 `target`,10 秒超时自动恢复

### 5. 局域网发现-重新扫描无反应

**根因**: `_buildDiscover()` 用 HTML 字符串拼接,onclick 属性引号嵌套错误。

**修复**: 整个函数改用 DOM API 构建,彻底消灭引号嵌套问题。重新扫描、Tab 切换、注册按钮均使用事件绑定。

### 6. 外网设备接入

**新增**: 设备管理 → 局域网发现 → 外网设备 Tab,提供:
- "手动添加外网设备"入口(设备ID + 名称)
- 接入说明(推送地址、Header、Body 格式)
- 外网设备通过 HTTP POST 推送数据到 `/api/ingest`,无需 MQTT

### 8. 报警界面无数据

**根因**: 同刷新按钮引号 bug。修复后可正常刷新。

### 9. 非ESP设备接入(HL7)

**新增**: 导航新增"📡 非ESP接入"标签页,包含:
- HTTP JSON 接入说明(URL/Header/Body 示例)
- HL7 v2.x 接入说明(ORU^R01 消息格式示例)
- 在线测试: 填写设备ID和数值,一键推送测试数据

### 10. 8266关联患者后监护界面无数据

**根因**: 监护界面 `refreshMonitor()` 只查 `/api/patients/{pid}/vitals`(手动录入的体征),不查关联设备的实时 telemetry。ESP8266 推送的是环境数据(temp/hum/pres),走 MQTT → telemetry 表,不是 patient_vitals 表。

**修复**: 在 `refreshMonitor()` 中新增加载关联设备列表 + 查询各设备实时 telemetry 的逻辑,在监护界面底部显示环境数据芯片(温度/湿度/气压/在线状态)。

---

## 后端新增

### /api/ingest 增强

- 支持外网设备通过 HTTP POST 推送 JSON 数据,无需 MQTT
- 设备不存在时自动注册
- 支持 Content-Type: text/plain(HL7 v2.x 文本)

### /api/hl7/parse (新增)

- POST 接收 HL7 v2.x 文本消息
- 解析 MSH/PID/OBR/OBX 段
- 返回结构化 JSON

### /api/patients/{pid}/devices 增强

- 返回中为每个设备补充 latest_telemetry 字段

---

## 接线速查(实测)

| 功能组 | 引脚 | GPIO | 通信 |
|--------|------|------|------|
| OLED | SDA→D4 | GPIO2 | u8g2 SW I2C |
| OLED | SCL→D5 | GPIO14 | u8g2 SW I2C |
| 传感器 | SDA→D7 | GPIO13 | Wire 硬件 I2C |
| 传感器 | SCL→D6 | GPIO12 | Wire 硬件 I2C |

OLED 地址 0x3C, AHT20=0x38, BMP280=0x77