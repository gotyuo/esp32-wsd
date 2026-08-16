#include "config_store.h"

ConfigStore  g_cfgStore;
DeviceConfig g_cfg;

static const char *NS = "envmon";

void ConfigStore::begin() {
    _prefs.begin(NS, false);
}

bool ConfigStore::load(DeviceConfig &cfg) {
    memset(&cfg, 0, sizeof(cfg));
    // 默认值
    cfg.mqtt_port       = 18830;
    cfg.report_interval = 10;
    cfg.temp_min = 5.0f;   cfg.temp_max = 40.0f;
    cfg.hum_min  = 20.0f;  cfg.hum_max  = 90.0f;
    cfg.pres_min = 950.0f; cfg.pres_max = 1050.0f;
    cfg.alarm_enabled = true;
    cfg.alarm_sound   = true;

    // 用 MAC 生成默认设备 ID 和默认热点名
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(cfg.device_id, sizeof(cfg.device_id), "envmon-%02x%02x%02x",
             mac[3], mac[4], mac[5]);
    snprintf(cfg.ap_ssid, sizeof(cfg.ap_ssid), "ENVMON-%02X%02X", mac[4], mac[5]);

    if (!_prefs.isKey("saved")) {
        return false;  // 从未配置过
    }
    String s;
    s = _prefs.getString("ssid", "");      s.toCharArray(cfg.wifi_ssid, sizeof(cfg.wifi_ssid));
    s = _prefs.getString("pass", "");      s.toCharArray(cfg.wifi_pass, sizeof(cfg.wifi_pass));
    s = _prefs.getString("apssid", cfg.ap_ssid);
    s.toCharArray(cfg.ap_ssid, sizeof(cfg.ap_ssid));
    s = _prefs.getString("host", "");      s.toCharArray(cfg.mqtt_host, sizeof(cfg.mqtt_host));
    s = _prefs.getString("user", "");      s.toCharArray(cfg.mqtt_user, sizeof(cfg.mqtt_user));
    s = _prefs.getString("mpass", "");     s.toCharArray(cfg.mqtt_pass, sizeof(cfg.mqtt_pass));
    cfg.mqtt_port       = _prefs.getUShort("port", 18830);
    cfg.report_interval = _prefs.getUShort("interval", 10);
    s = _prefs.getString("devid", cfg.device_id);
    s.toCharArray(cfg.device_id, sizeof(cfg.device_id));

    cfg.temp_min = _prefs.getFloat("tmin", cfg.temp_min);
    cfg.temp_max = _prefs.getFloat("tmax", cfg.temp_max);
    cfg.hum_min  = _prefs.getFloat("hmin", cfg.hum_min);
    cfg.hum_max  = _prefs.getFloat("hmax", cfg.hum_max);
    cfg.pres_min = _prefs.getFloat("pmin", cfg.pres_min);
    cfg.pres_max = _prefs.getFloat("pmax", cfg.pres_max);
    cfg.alarm_enabled = _prefs.getBool("alarm", true);
    cfg.alarm_sound   = _prefs.getBool("sound", true);
    return true;
}

bool ConfigStore::save(const DeviceConfig &cfg) {
    _prefs.putString("ssid", cfg.wifi_ssid);
    _prefs.putString("pass", cfg.wifi_pass);
    _prefs.putString("apssid", cfg.ap_ssid);
    _prefs.putString("host", cfg.mqtt_host);
    _prefs.putString("user", cfg.mqtt_user);
    _prefs.putString("mpass", cfg.mqtt_pass);
    _prefs.putUShort("port", cfg.mqtt_port);
    _prefs.putUShort("interval", cfg.report_interval);
    _prefs.putString("devid", cfg.device_id);
    _prefs.putFloat("tmin", cfg.temp_min);
    _prefs.putFloat("tmax", cfg.temp_max);
    _prefs.putFloat("hmin", cfg.hum_min);
    _prefs.putFloat("hmax", cfg.hum_max);
    _prefs.putFloat("pmin", cfg.pres_min);
    _prefs.putFloat("pmax", cfg.pres_max);
    _prefs.putBool("alarm", cfg.alarm_enabled);
    _prefs.putBool("sound", cfg.alarm_sound);
    _prefs.putBool("saved", true);
    return true;
}

bool ConfigStore::clear() {
    return _prefs.clear();
}
