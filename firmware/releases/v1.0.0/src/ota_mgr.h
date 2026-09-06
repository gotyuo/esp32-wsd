#pragma once

/**
 * OTA 固件升级管理器 - 头文件
 *
 * 外部函数（供 main.cpp 调用）:
 *   ota_setup()          启动时初始化，含 boot_count 自动回滚判定
 *   ota_check(bool force) 检查服务器版本，若有新版则自动下载升级
 *   ota_set_server(host, token)  配置 OTA 服务器地址（可选）
 */

#ifdef __cplusplus
extern "C" {
#endif

void ota_setup(void);
void ota_check(bool force);
void ota_set_server(const char *host, const char *token);

#ifdef __cplusplus
}
#endif