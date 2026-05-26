/*
 * TransTheFlip — BLE Remote Bad USB
 * Copyright (C) 2026 Romain Champliau
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */
#pragma once

#include "trans_the_flip.h"
#include <stdbool.h>

/**
 * @brief Initialise le service BLE serial (NUS / Nordic UART Service).
 *        Enregistre le callback qui mettra les événements dans l'event_queue.
 * @param app  Pointeur vers l'application.
 * @return true si réussi.
 */
bool ttf_bt_init(TransTheFlipApp* app);

/**
 * @brief Désenregistre le callback BLE serial et libère les ressources.
 * @param app  Pointeur vers l'application.
 */
void ttf_bt_deinit(TransTheFlipApp* app);

/**
 * @brief Envoie un message de statut au PC connecté via BLE TX.
 *        Utilisé pour confirmer la réception, l'envoi, ou signaler une annulation.
 * @param msg  Chaîne null-terminée (ex: "OK\n", "CANCEL\n", "ERR\n").
 */
void ttf_bt_send_status(const char* msg);

/**
 * @brief À appeler dès qu'une connexion BLE est établie.
 *        Désactive le mode RPC et réamorce le flow control.
 */
void ttf_bt_on_connect(void);

/**
 * @brief Signale au serial service que notre buffer d'application est libre.
 *        À appeler depuis le thread principal UNIQUEMENT (pas depuis un callback
 *        BLE, car le firmware tient buff_size_mtx pendant l'appel du callback).
 */
void ttf_bt_notify_ready(void);
