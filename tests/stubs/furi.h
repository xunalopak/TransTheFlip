#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
void furi_delay_ms(uint32_t ms);
void* furi_record_open(const char* record);
void furi_record_close(const char* record);
