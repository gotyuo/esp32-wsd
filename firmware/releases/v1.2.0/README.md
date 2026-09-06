# envmon 固件 v1.2.0

> 医生企微消息 → 设备屏幕管道 · 版本固化基线
> 固化日期: 2026-09-06
> 前置基线: v1.1.0

---

## 本版新增

| 功能 | 说明 |
|------|------|
| 企微消息管道 | 医生通过企业微信发消息 → 服务器回调 → MQTT → 设备屏幕显示 |
| 提醒消息显示 | 新增第4页"消息"页面，自动跳转+60秒过期 |
| MQTT reminder 主题 | 订阅 `envmon/{id}/reminder`，接收 `{"text":"..."}` |

---

## 端到端链路

```
医生企微发消息
  → 企微推送加密XML到 /api/wechat/callback
    → 服务器验证签名 + AES解密
    → 查 doctors.wechat_userid → patients.doctor → patient_devices
    → MQTT publish envmon/{id}/reminder {"text":"..."}
    → 设备订阅收到
    → 屏幕跳转第4页显示消息（60秒后过期）
```

---

## 固件改动

### mqtt_mgr.h / mqtt_mgr.cpp

- 新增 `_topicRem` 主题缓冲区（`envmon/{id}/reminder`）
- 新增 `applyReminderPayload(json)` — 解析 `{"text":"..."}` 存入缓冲区
- 新增 `takeReminderText()` — 主循环读取后清空
- 新增 `hasReminder()` — 查询是否有新消息
- 新增 `_reminderText` / `_reminderPending` / `_reminderTime` 成员

### main_esp32s3_tft7735.cpp

- `NUM_PAGES` 从 3 改为 4
- 新增第4页"消息"显示（最多3行，每行15字符）
- 主循环检查 `hasReminder()` → 跳转第4页 + 强制刷新
- 消息 60 秒后自动过期清除

### 服务器端 (main.py)

- 新增 `/api/wechat/callback` 端点（async）
- AES-256-CBC 加解密（openssl 命令行，无需额外 Python 库）
- SHA1 签名验证
- 查询链路：`doctors.wechat_userid` → `patients.doctor` → `patient_devices`
- 转发到 `envmon/{device_id}/reminder` MQTT 主题

---

## 固件参数

| 项目 | 值 |
|------|-----|
| 固件大小 | 881728 bytes |
| Flash 使用率 | 26.4% (881728 / 3342336) |
| RAM 使用率 | 15.2% (49968 / 327680) |
| 编译环境 | esp32-s3-tft7735 |
| 编译工具链 | xtensa-esp32s3 @ 8.4.0 |
| PlatformIO | espressif32 @ 6.5.0 |

---

## 文件清单

```
releases/v1.2.0/
├── firmware_bin/
│   ├── bootloader.bin    (15104 bytes @ 0x00000000)
│   ├── partitions.bin    (3072 bytes @ 0x00008000)
│   └── firmware.bin      (881728 bytes @ 0x00010000)
├── src/                  (30 source files)
├── flash.sh              (烧录脚本)
├── partitions_ota.csv    (分区表)
└── README.md             (本文件)
```

---

## 烧录

```bash
cd releases/v1.2.0 && bash flash.sh
# 或手动:
pio run -e esp32-s3-tft7735 -t upload --upload-port /dev/ttyACM0
```

---

## 版本控制规则

- `releases/v1.2.0/` 是只读基线，不可修改
- 后续改动需在 `releases/v1.3.0/` 中
- 工作区: `src_esp32s3_tft7735/`
- 工作副本: `release/esp32s3-tft7735/`

---

## 对比 v1.1.0

| 项目 | v1.1.0 | v1.2.0 | 差异 |
|------|--------|--------|------|
| 固件大小 | 880000 | 881728 | +1728 |
| Flash | 26.3% | 26.4% | +0.1% |
| RAM | 15.2% | 15.2% | 0 |
| 显示页数 | 3 | 4 | +1 (消息页) |
| MQTT 订阅 | tts + config | tts + config + reminder | +reminder |
| 企微回调 | 无 | `/api/wechat/callback` | 新增 |