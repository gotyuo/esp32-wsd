# 固件补丁：让 beacon 探针携带「项目标识」，实现多项目共存

- 补丁版本：`20261006-v1.0`
- 适用源码：`firmware/esp32-7oled/src/net_mgr.cpp`（TFT7735 2.0.0）
- 改动量：**3 处**，不改变任何上报逻辑，不影响已打开的配网页面
- 本机无 espressif32 工具链，**需你用 Arduino IDE 编译上传**

---

## 背景：为什么必须加这一步

固件原本的发现探针是**裸字符串**，不带任何身份：

```cpp
static const char DISC_REQ[] = "ENVMON?";      // ← 就这一串，没有 pid 没有 did
```

后果：**同一局域网里所有 EnvMon 服务器都会应答**，设备拿到谁的回复全看谁先到 → 多项目必然串。
所以服务端再怎么写"自动发现"，只要设备不报身份，就无法区分项目。

---

## 补丁（L1：编译期 PROJECT_ID）

> 每个项目编译一份固件，PID 在编译期固定。改动最小，够用。

```diff
--- a/firmware/esp32-7oled/src/net_mgr.cpp
+++ b/firmware/esp32-7oled/src/net_mgr.cpp
@@ -476,8 +476,14 @@ static const int DISC_PORT = 12091;
 static const char DISC_MCAST_IP[] = "239.255.1.1";
-static const char DISC_REQ[] = "ENVMON?";
+
+// ---- 多项目共存：探针携带项目标识 pid，服务端据此路由到本项目专属服务器 ----
+#ifndef PROJECT_ID
+  #define PROJECT_ID "default"        // ← 每个项目编译前改这里（也可 -DPROJECT_ID=clinic-a 传入）
+#endif
+static const char DISC_REQ_FMT[] = "{\"probe\":\"EnvMon\",\"pid\":\"%s\",\"did\":\"%s\"}";
+
 static const uint32_t DISC_SEND_INTERVAL = 4000;   // 每 4s 发一次
 static const uint32_t DISC_TIMEOUT = 45000;       // 45s 超时回 AP
@@ -540,9 +546,14 @@ int NetManager::discoverLoop(uint32_t now) {
     // 周期 beacon
     if ((now - _discLastSent) > DISC_SEND_INTERVAL) {
         _discLastSent = now;
         _udp.beginPacket(IPAddress(239, 255, 1, 1), DISC_PORT);
-        _udp.print(DISC_REQ);
+
+        // 带身份的探针：让服务端能按 pid 分流；did 为空时自动用 MAC 兜底
+        char _probe[192];
+        const char *_did = (_cfg->device_id[0] != '\0') ? _cfg->device_id
+                                                        : WiFi.macAddress().c_str();
+        snprintf(_probe, sizeof(_probe), DISC_REQ_FMT, PROJECT_ID, _did);
+        _udp.print(_probe);
+
         _udp.endPacket();
     }
```

改完后的探针样子：

```json
{"probe":"EnvMon","pid":"clinic-a","did":"esp32-4d3f54"}
```

应答格式**完全不变**（固件解析逻辑不用动）：

```json
{"ip":"192.168.2.220","port":18830,"user":"envmon","pass":"envmon"}
```

### 编译方式

Arduino IDE 不支持直接在 UI 传宏时，就改源码里的 `#define PROJECT_ID "clinic-a"`；
PlatformIO 可在 `platformio.ini` 加：

```ini
build_flags = -DPROJECT_ID=\"clinic-a\"
```

---

## 补丁 L2（进阶）：project_id 运行时可改

不想为每个项目编译一份固件时，把 PID 存进 NVS、配网页加一个输入框：

| 文件 | 改动 |
|---|---|
| `config_store.h` | `DeviceConfig` 加 `char project_id[24]` |
| `config_store.cpp` | `load()`/`save()` 加 `get/putString("pid", ...)`（默认 `"default"`） |
| `net_mgr.cpp` | 配网表单加 `<input name="pid">`；`handleSave()` 取 `web.arg("pid")`；探针里用 `_cfg->project_id` 取代宏 |

工作量约 20 行，代价是 config 结构体变化（旧 NVS 配置会读到空 PID，需给默认值兜底）。

---

## 兼容性说明

| 场景 | 行为 |
|---|---|
| 未打补丁的旧设备 + 本服务端 | 发裸 `ENVMON?`，服务端按 `default` 应答（**单项目环境开 default 即可照常用**） |
| 打了补丁的设备 + 本服务端 | 按 `pid` 精确路由，不匹配则不答 |
| 打了补丁的设备 + 旧服务端 | 旧服务端通常忽略请求内容直接回固定 IP → 仍能连上（退化为单项目行为） |

即：**可灰度推进**，不必一次性替换所有设备。
