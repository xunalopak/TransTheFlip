#pragma once
#include "furi.h"
typedef struct Storage Storage;
typedef struct File File;
#define RECORD_STORAGE "storage"
#define FSAM_READ 0
#define FSOM_OPEN_EXISTING 0
File* storage_file_alloc(Storage* storage);
bool storage_file_open(File* file, const char* path, int access, int mode);
uint16_t storage_file_read(File* file, void* buffer, uint16_t length);
void storage_file_close(File* file);
void storage_file_free(File* file);
