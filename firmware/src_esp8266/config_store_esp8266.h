#pragma once
// ============================================================
// 配置管理 (ESP8266) - 使用 EEPROM 模拟 NVS
// ESP8266 无 Preferences 库，用 EEPROM 读写配置
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
    char     mqtt_host[MAX_HOST_LEN + 1];
    uint16_t mqtt_port;
    char     mqtt_user[MAX_USER_LEN + 1];
    char     mqtt_pass[MAX_PASS_LEN + 1];
    char     device_id[MAX_DEV_LEN + 1];
    uint16_t report_interval;
    float    temp_min, temp_max;
    float    hum_min,  hum_max;
    float    pres_min, pres_max;
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
