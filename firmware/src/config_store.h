#pragma once
// ============================================================
// 配置管理：使用 ESP32-S3 内部 NVS（Preferences）保存用户配置
// 掉电不丢失，配网一次即可长期使用
// ============================================================
#include <Arduino.h>
#include <Preferences.h>

#define MAX_SSID_LEN   32
#define MAX_PASS_LEN   64
#define MAX_HOST_LEN   64
#define MAX_USER_LEN   32
#define MAX_DEV_LEN    24

struct DeviceConfig {
    // WiFi
    char     wifi_ssid[MAX_SSID_LEN + 1];
    char     wifi_pass[MAX_PASS_LEN + 1];
    // AP 热点名称
    char     ap_ssid[MAX_SSID_LEN + 1];
    // MQTT 服务器
    char     mqtt_host[MAX_HOST_LEN + 1];
    uint16_t mqtt_port;
    char     mqtt_user[MAX_USER_LEN + 1];
    char     mqtt_pass[MAX_PASS_LEN + 1];
    char     device_id[MAX_DEV_LEN + 1];
    // 服务器接入模式: 0=局域网自动发现(LAN beacon), 1=手动指定(填入 mqtt_host)
    uint8_t  server_mode;
    // 上报间隔（秒）
    uint16_t report_interval;
    // 报警阈值
    float    temp_min, temp_max;
    float    hum_min,  hum_max;
    float    pres_min, pres_max;
    bool     alarm_enabled;
    bool     alarm_sound;    // 报警时蜂鸣器发声开关

    bool has_wifi() const { return wifi_ssid[0] != '\0'; }
    bool has_mqtt() const { return mqtt_host[0] != '\0'; }
};

class ConfigStore {
public:
    void begin();
    bool load(DeviceConfig &cfg);
    bool save(const DeviceConfig &cfg);
    bool clear();
    // 加载后兜底：MQTT 凭据缺失时补默认值
    void applyDefaults(DeviceConfig &cfg);

private:
    Preferences _prefs;
};

extern ConfigStore  g_cfgStore;
extern DeviceConfig g_cfg;
