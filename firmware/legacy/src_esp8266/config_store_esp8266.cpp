// ============================================================
// 配置管理 ESP8266 - EEPROM 实现
// ============================================================
#include "config_store_esp8266.h"
#include <cstring>
#include <ESP8266WiFi.h>

ConfigStore  g_cfgStore;
DeviceConfig g_cfg;

struct EepromHeader {
    uint16_t magic;
    uint16_t len;
};

void ConfigStore::begin() {
    EEPROM.begin(EEPROM_SIZE);
}

bool ConfigStore::load(DeviceConfig &cfg) {
    memset(&cfg, 0, sizeof(cfg));
    cfg.mqtt_port       = 18830;
    cfg.server_mode     = 0;          // 默认=自动发现(LAN)
    cfg.report_interval = 10;
    cfg.temp_min = 5.0f;   cfg.temp_max = 40.0f;
    cfg.hum_min  = 20.0f;  cfg.hum_max  = 90.0f;
    cfg.pres_min = 950.0f; cfg.pres_max = 1050.0f;
    cfg.alarm_enabled = true;
    cfg.alarm_sound   = true;

    // 用 MAC 生成默认设备 ID 和热点名
    uint8_t mac[6];
    WiFi.macAddress(mac);
    snprintf(cfg.device_id, sizeof(cfg.device_id), "envmon8266-%02x%02x%02x",
             mac[3], mac[4], mac[5]);
    snprintf(cfg.ap_ssid, sizeof(cfg.ap_ssid), "ENVMON8266-%02X%02X", mac[4], mac[5]);

    strcpy(cfg.mqtt_user, "envmon");
    strcpy(cfg.mqtt_pass, "envmon");

    EepromHeader hdr;
    EEPROM.get(0, hdr);
    if (hdr.magic != EEPROM_MAGIC || hdr.len != sizeof(DeviceConfig)) {
        return false;
    }
    EEPROM.get(sizeof(EepromHeader), cfg);
    return true;
}

bool ConfigStore::save(const DeviceConfig &cfg) {
    EepromHeader hdr;
    hdr.magic = EEPROM_MAGIC;
    hdr.len   = sizeof(DeviceConfig);
    EEPROM.put(0, hdr);
    EEPROM.put(sizeof(EepromHeader), cfg);
    return EEPROM.commit();
}

void ConfigStore::applyDefaults(DeviceConfig &cfg) {
    if (cfg.mqtt_host[0] != '\0' && cfg.mqtt_user[0] == '\0') {
        strcpy(cfg.mqtt_user, "envmon");
    }
    if (cfg.mqtt_pass[0] == '\0') {
        strcpy(cfg.mqtt_pass, "envmon");
    }
}

bool ConfigStore::clear() {
    EepromHeader hdr;
    hdr.magic = 0;
    hdr.len   = 0;
    EEPROM.put(0, hdr);
    return EEPROM.commit();
}
