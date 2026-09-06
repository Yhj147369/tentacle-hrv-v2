/*
 * toy_profiles.h —— Tentacle HRV 设备类型(Device Profile)适配框架
 * Copyright (C) 2026 Yi Hengjun (伊恒君)
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * 背景：社区(amlyxts @ Koishi topic/13546 #7)请求"体感设备 B兼容"。
 * 本框架把 ESP32 的"玩具通道"从写死常量参数化为设备类型 Profile：
 *   - JUMP_EGG（默认） = 现有体感设备 A/振动类：UUID 0xFFE0/0xFFE1、
 *     报文 {0xA1,0x02,intensity,checksum,0xB3}、物理上限 60 —— 行为逐字节不变；
 *   - MALE_MST（预留） = 男用伸缩/多通道类（体感设备 B）：具体 BLE 协议（UUID/
 *     数据包/伸缩/加热等）需实物逆向或选用 buttplug(stpihkal) 已文档化协议后
 *     填入 —— 未实现前 encode 返回 0，绝不发送臆造的协议字节。
 *
 * 切换设备类型（编译期，默认体感设备 A）：
 *   在 esp32_firmware.ino 顶部修改 ACTIVE_TOY_PROFILE，或在命令行加
 *   --build-property build.extra_flags=-DACTIVE_TOY_PROFILE=TOY_PROFILE_MALE_MST
 *
 * 详见仓库文档：docs/体感设备适配说明.md
 */

#ifndef TOY_PROFILES_H
#define TOY_PROFILES_H

#include <stddef.h>
#include <stdint.h>

/* ============ 设备类型枚举 ============ */
typedef enum {
  TOY_PROFILE_JUMP_EGG = 0,   /* 默认：体感设备 A/单振动通道（现协议，保持不变） */
  TOY_PROFILE_MALE_MST = 1,   /* 预留：男用伸缩/多通道设备（体感设备 B类） */
  TOY_PROFILE_COUNT
} ToyProfileId;

/* ============ 通道位掩码（描述设备执行器，供未来多通道用） ============ */
#define TOY_CH_NONE    0x00u
#define TOY_CH_VIB     0x01u   /* 振动 */
#define TOY_CH_STROKE  0x02u   /* 伸缩 */
#define TOY_CH_AIR     0x04u   /* 气压 */
#define TOY_CH_ROTATE  0x08u   /* 旋转 */
#define TOY_CH_HEAT    0x10u   /* 加热（默认不开放，防烫伤） */

/* ============ 体感设备 A(默认) profile 常量：现协议，保持不变 ============ */
#define TOY_JUMP_EGG_SERVICE_UUID "0000FFE0-0000-1000-8000-00805F9B34FB"
#define TOY_JUMP_EGG_CHAR_UUID    "0000FFE1-0000-1000-8000-00805F9B34FB"
#define TOY_JUMP_EGG_MAX_INTENSITY 60u

/* ============ 体感设备 B类(预留) 占位常量 ============
 * TODO(设备到货逆向后填写)：以下为占位值，禁止用真实连接。
 * 逆向步骤见 docs/体感设备适配说明.md「接入步骤」：
 *   nRF Connect 抓服务/特征 UUID → 填到下方宏 → 实现 toyMaleMstEncode()
 *   的协议字节 → 编译烧录。可优先对照 buttplug.io/stpihkal 对应机型页面。 */
#define MALE_MST_SERVICE_UUID_PLACEHOLDER "00000000-0000-0000-0000-000000000000"
#define MALE_MST_CHAR_UUID_PLACEHOLDER    "00000000-0000-0000-0000-000000000000"

/* 一次 SET 指令的编码器：
 *   输入 intensity(0..profile 上限)、waveform(0..3，见 WAVE_*)；
 *   把待写字节填入 out（容量 cap），返回字节数；返回 0 表示"无字节可发"
 *   （预留 profile 未实现 / 指令无动作）。 */
typedef size_t (*ToyEncodeFn)(int intensity, int waveform,
                              uint8_t *out, size_t cap);

/* ============ Profile 描述结构 ============ */
typedef struct {
  ToyProfileId id;          /* 枚举 id */
  const char  *name;        /* "jump_egg" / "male_mst" */
  const char  *service_uuid;/* 广播过滤 + getService */
  const char  *tx_uuid;     /* 写特征 */
  const char  *rx_uuid;     /* 通知特征（可空，暂未使用） */
  uint8_t      max_intensity;/* 物理强度上限（SET 钳制用） */
  uint8_t      channels;    /* TOY_CH_* 位掩码 */
  ToyEncodeFn  encode;      /* SET/STOP 编码器 */
} ToyProfile;

/* profile 表（定义见 toy_profiles.cpp），按 ToyProfileId 索引 */
extern const ToyProfile toy_profiles[TOY_PROFILE_COUNT];

/* 当前活动 profile id（运行时变量，初值 = esp32_firmware.ino 顶部的
 * ACTIVE_TOY_PROFILE 宏；将来可扩展串口命令 PROFILE <id> 白名单切换） */
extern ToyProfileId g_active_profile_id;

/* 返回当前活动 profile（越界时回退体感设备 A） */
const ToyProfile *toyActiveProfile(void);

#endif /* TOY_PROFILES_H */
