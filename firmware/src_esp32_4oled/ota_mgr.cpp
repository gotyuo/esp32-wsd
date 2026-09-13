// ============================================================
// OTA 固件升级管理器 (ESP32-S3)
//
// 功能:
//   1. 启动时检查 boot_count，判断上一次更新是否健康（<3 次则回滚）
//   2. 定时/手动触发检查 /api/ota/version，比较本地 vs 远程
//   3. 版本新 → HTTP 下载 /api/ota/image → Update.write → esp_restart
//   4. 升级后 boot_count 归零，连续 3 次启动成功 = 新版本健康
//
// 触发:
//   - MQTT 主题 envmon/{id}/ota
//   - 串口命令 otacheck
//   - 启动时自动检查一次
// ============================================================
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <Preferences.h>

// 头文件引用
#include "config_store.h"
#include "net_mgr.h"
#include "mqtt_mgr.h"

namespace ota {

static Preferences _prefs;
static String g_ota_host;     // "192.168.1.100:8627"
static String g_ota_token;

static const char NAMESPACE[] = "ota";

// ---------- NVS boot_count 回滚 ----------
static void prefsRW(bool readOnly) {
    if (readOnly) _prefs.begin(NAMESPACE, true);
    else          _prefs.begin(NAMESPACE, false);
}

void bootAutoRollback() {
    prefsRW(true);
    uint32_t bc = _prefs.getUInt("boot", 0);
    if (bc > 0 && bc < 3) {
        Serial.printf("[OTA] boot_count=%u (<3)，回滚到上一个固件\n", bc);
        _prefs.end();
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
    // 无论有无数据，先记录本版本启动一次
    _prefs.putUInt("boot", bc + 1);
    if (bc + 1 >= 3) {
        Serial.printf("[OTA] boot_count=%u，新版本健康，清除状态\n", bc + 1);
        _prefs.remove("boot");
    } else {
        Serial.printf("[OTA] boot_count=%u/3\n", bc + 1);
    }
    _prefs.end();
}

// ---------- 版本管理 ----------
static String getLocalVersion() {
    prefsRW(true);
    String v = _prefs.getString("ver", "");
    _prefs.end();
    if (v.isEmpty()) return FW_VERSION;
    return v;
}

static void setLocalVersion(const char *v) {
    prefsRW(false);
    _prefs.putString("ver", v);
    _prefs.end();
    Serial.printf("[OTA] 记录版本 %s\n", v);
}

static int versionToInt(const String &v) {
    int major=0, minor=0, patch=0;
    char buf[16];
    v.toCharArray(buf, sizeof(buf));
    char *p1=strtok(buf,"."), *p2=strtok(nullptr,"."), *p3=strtok(nullptr,".");
    if (p1) major=atoi(p1);
    if (p2) minor=atoi(p2);
    if (p3) patch=atoi(p3);
    return major*10000 + minor*100 + patch;
}

// ---------- 配置 ----------
void setServer(const String &host, const String &token) {
    g_ota_host = host;
    g_ota_token = token;
}

// ---------- 通信 ----------
static bool fetchVersion(String &out) {
    if (g_ota_host.isEmpty()) return false;
    WiFiClient c;
    HTTPClient http;
    http.setTimeout(10000);
    String url = "http://" + g_ota_host + "/api/ota/version";
    if (!http.begin(c, url)) {
        Serial.println("[OTA] HTTP begin failed (version)");
        return false;
    }
    if (!g_ota_token.isEmpty()) {
        http.addHeader("Authorization", "Bearer " + g_ota_token);
    }
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[OTA] GET /api/ota/version HTTP %d\n", code);
        http.end();
        return false;
    }
    String body = http.getString();
    http.end();
    // 解析 "version":"1.7.0"
    int p = body.indexOf("\"version\"");
    if (p < 0) return false;
    int q1 = body.indexOf('"', p + 9);
    int q2 = body.indexOf('"', q1 + 1);
    if (q1 < 0 || q2 <= q1) return false;
    out = body.substring(q1 + 1, q2);
    return true;
}

static bool downloadUpgrade(const String &remoteVer) {
    Serial.printf("[OTA] 开始升级 本地=%s -> 远程=%s\n", FW_VERSION, remoteVer.c_str());
    WiFiClient c;
    HTTPClient http;
    http.setTimeout(60000);
    String url = "http://" + g_ota_host + "/api/ota/image";
    if (!http.begin(c, url)) {
        Serial.println("[OTA] HTTP begin failed (image)");
        return false;
    }
    if (!g_ota_token.isEmpty()) {
        http.addHeader("Authorization", "Bearer " + g_ota_token);
    }
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[OTA] GET /api/ota/image HTTP %d\n", code);
        http.end();
        return false;
    }

    int total = http.getSize();
    Serial.printf("[OTA] 下载固件 %d bytes...\n", total);

    // 擦除 + 准备下一个 OTA 分区
    if (Update.begin((size_t)total)) {
        Serial.printf("[OTA] Update.begin OK (size=%d)\n", total);
    } else {
        Serial.printf("[OTA] Update.begin 失败: %s\n", Update.errorString());
        http.end();
        return false;
    }

    // UpdateClass 不继承 Stream，需要显式转型 + 分块写入
    WiFiClient *cptr = http.getStreamPtr();
    if (!cptr) {
        Serial.println("[OTA] getStreamPtr null");
        Update.abort();
        http.end();
        return false;
    }
    uint8_t buf[4096];
    int written = 0;
    while (written < total) {
        int toRead = min(4096, total - written);
        int r = cptr->read(buf, (size_t)toRead);
        if (r <= 0) {
            Serial.printf("[OTA] 下载中断 %d/%d\n", written, total);
            Update.abort();
            http.end();
            return false;
        }
        if (!Update.write(buf, (size_t)r)) {
            Serial.printf("[OTA] Update.write 失败: %s\n", Update.errorString());
            http.end();
            return false;
        }
        written += r;
    }
    http.end();

    if (!Update.end(true)) {
        Serial.printf("[OTA] Update.end 失败: %s\n", Update.errorString());
        return false;
    }

    setLocalVersion(remoteVer.c_str());
    Serial.printf("[OTA] 升级完成，正在重启...\n");
    delay(200);
    ESP.restart();
    return true;
}

// ---------- 检查 ----------
static bool g_running = false;

void check(bool force) {
    if (g_running) return;
    if (g_ota_host.isEmpty() && !force) return;
    if (!g_net.wifiConnected()) {
        Serial.println("[OTA] WiFi 未连接，跳过检查");
        return;
    }
    g_running = true;
    Serial.println("[OTA] ====== 检查固件版本 ======");
    String remoteVer;
    if (!fetchVersion(remoteVer)) {
        Serial.println("[OTA] 无法获取服务器版本");
        g_running = false;
        return;
    }
    int localInt  = versionToInt(getLocalVersion());
    int remoteInt = versionToInt(remoteVer);
    Serial.printf("[OTA] 本地=%s(%d) 远程=%s(%d)\n",
                  getLocalVersion().c_str(), localInt,
                  remoteVer.c_str(), remoteInt);
    if (remoteInt > localInt) {
        downloadUpgrade(remoteVer);
    } else {
        Serial.println("[OTA] 已是最新版本");
    }
    g_running = false;
}

} // namespace ota

// ---------- C 接口 ----------
extern "C" {
void ota_setup() {
    ota::bootAutoRollback();
}
void ota_check(bool force) {
    ota::check(force);
}
void ota_set_server(const char *host, const char *token) {
    ota::setServer(host ? host : "", token ? token : "");
}
}