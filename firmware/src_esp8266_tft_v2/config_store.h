#pragma once
// ============================================================
// 配置管理 (ESP8266) — EEPROM 存储
// ============================================================
#include <Arduino.h>
#include <EEPROM.h>

#define MAX_STR_LEN 64
#define EEPROM_SIZE 512
#define EEPROM_MAGIC 0xE826

struct DeviceConfig {
    char     wifi_ssid[33];
    char     wifi_pass[65];
    char     ap_ssid[33];
    char     mqtt_host[65];
    uint16_t mqtt_port;
    char     mqtt_user[33];
    char     mqtt_pass[65];
    char     device_id[25];
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