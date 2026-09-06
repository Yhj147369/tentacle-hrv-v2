/*
 * toy_profiles.cpp —— Tentacle HRV 设备类型(Device Profile)表与编码器实现
 * Copyright (C) 2026 Yi Hengjun (伊恒君)
 *
 * GPL-3.0，见 toy_profiles.h 头注释与本仓库 LICENSE。
 *
 * 新增一种设备类型：在这里追加一个 ToyProfile 表项 + 对应 encode 函数即可，
 * 主循环 / 串口协议 / server.py 均无需改动。体感设备 A(TOY_PROFILE_JUMP_EGG)是
 * 默认表项，编码逐字节复刻原 esp32_firmware.ino 的 sendToToy()，行为不变。
 */

#include "toy_profiles.h"

/* ============ 体感设备 A(默认)：现协议编码，逐字节不变 ============
 * 报文 5 字节：{0xA1, 0x02, intensity, checksum, 0xB3}
 *   checksum = 0xA1 ^ 0x02 ^ intensity
 * 波形 waveform(0..3) 目前未进入报文 —— 保留原 TODO。 */
static size_t jumpEggEncode(int intensity, int waveform,
                            uint8_t *out, size_t cap) {
  (void)waveform; /* TODO: 逆向出玩具协议后，把波形模式(0-3)编码进数据包的对应字节 */
  if (out == NULL || cap < 5) return 0;
  out[0] = 0xA1;
  out[1] = 0x02;
  out[2] = (uint8_t)(intensity & 0xFF);
  out[3] = out[0] ^ out[1] ^ out[2];
  out[4] = 0xB3;
  return 5;
}

/* ============ 预留：男用设备 / 体感设备 B类（伸缩·振动·气动等） ============
 * TODO(设备到货逆向后实现)：具体机型协议千差万别（UUID、包格式、是否需要
 * 握手/通知订阅、伸缩位置/速度、加热等各通道编码），禁止凭空臆造字节。
 * 实现前请参照 docs/体感设备适配说明.md「接入步骤」与 buttplug.io/stpihkal
 * 对应机型页（如 Kiiroo/Fleshlight 伸缩为 2 字节位置+速度；Lovense 为分号
 * 文本指令 Vibrate:0-20; 等）。未实现时返回 0 = 不发送任何字节。 */
static size_t maleMstEncode(int intensity, int waveform,
                            uint8_t *out, size_t cap) {
  (void)intensity; (void)waveform; (void)out; (void)cap;
  return 0; /* 预留未实现 */
}

/* ============ Profile 表（按下标 = ToyProfileId） ============ */
const ToyProfile toy_profiles[TOY_PROFILE_COUNT] = {
  /* TOY_PROFILE_JUMP_EGG —— 默认体感设备 A，UUID/上限/报文与原固件完全一致 */
  {
    TOY_PROFILE_JUMP_EGG,
    "jump_egg",
    TOY_JUMP_EGG_SERVICE_UUID,
    TOY_JUMP_EGG_CHAR_UUID,
    NULL,                          /* rx：体感设备 A无通知订阅 */
    TOY_JUMP_EGG_MAX_INTENSITY,    /* 60 */
    TOY_CH_VIB,                    /* 通道：振动 */
    jumpEggEncode,
  },
  /* TOY_PROFILE_MALE_MST —— 预留男用设备：占位 UUID + 未实现编码 */
  {
    TOY_PROFILE_MALE_MST,
    "male_mst",
    MALE_MST_SERVICE_UUID_PLACEHOLDER, /* TODO: 逆向所得服务 UUID */
    MALE_MST_CHAR_UUID_PLACEHOLDER,    /* TODO: 逆向所得写特征 UUID */
    NULL,                          /* TODO: 逆向所得通知特征 UUID（如需订阅） */
    60,                            /* TODO: 按实物调整（如 Lovense 振动 0-20、伸缩位置/速度 0-99） */
    (uint8_t)(TOY_CH_VIB | TOY_CH_STROKE), /* TODO: 按实物逆向调整通道位 */
    maleMstEncode,
  },
};

const ToyProfile *toyActiveProfile(void) {
  int id = (int)g_active_profile_id;
  if (id < 0 || id >= (int)TOY_PROFILE_COUNT) {
    id = TOY_PROFILE_JUMP_EGG; /* 越界回退默认体感设备 A */
  }
  return &toy_profiles[id];
}
