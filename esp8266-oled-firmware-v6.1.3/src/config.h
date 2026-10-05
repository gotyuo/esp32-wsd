#pragma once
// ============================================================
// 配置管理 (ESP8266) - 使用 EEPROM 模拟 NVS
// v4.0: 新增 sp_o2/pr_hr 报警阈值；MQTT 主机/IP 支持更明确
// ============================================================
#include <Arduino.h>
#include <EEPROM.h>

#define MAX_SSID_LEN   32
#define MAX_PASS_LEN   64
#define MAX_HOST_LEN   64
#define MAX_USER_LEN   32
#define MAX_DEV_LEN    24

#define EEPROM_SIZE 512
#define EEPROM_MAGIC 0xE826

struct DeviceConfig {
    char     wifi_ssid[MAX_SSID_LEN + 1];
    char     wifi_pass[MAX_PASS_LEN + 1];
    char     ap_ssid[MAX_SSID_LEN + 1];
    char     mqtt_host[MAX_HOST_LEN + 1];   // 服务器 IP / 域名
    uint16_t mqtt_port;
    char     mqtt_user[MAX_USER_LEN + 1];
    char     mqtt_pass[MAX_PASS_LEN + 1];
    char     device_id[MAX_DEV_LEN + 1];
    uint8_t  server_mode;                   // 0=LAN自动发现 1=手动
    uint16_t report_interval;               // 上报间隔(秒)
    // 环境报警阈值
    float    temp_min, temp_max;
    float    hum_min,  hum_max;
    float    pres_min, pres_max;
    // 体征报警阈值
    float    spo2_min;                      // 血氧下限(%)
    float    hr_min, hr_max;                // 心率范围(bpm)
    bool     alarm_enabled;
    bool     alarm_sound;

    bool has_wifi() const { return wifi_ssid[0] != '\0'; }
    bool has_mqtt() const { return mqtt_host[0] != '\0'; }
};

class ConfigStore {
public:
    void begin();
    bool load(DeviceConfig &cfg);
    bool save(const DeviceConfig &cfg);
    bool clear();
    void applyDefaults(DeviceConfig &cfg);
};

extern ConfigStore  g_cfgStore;
extern DeviceConfig g_cfg;
