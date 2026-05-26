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
#include <gui/gui.h>
#include <gui/canvas.h>

/**
 * @brief Callback de rendu GUI.
 *        Appelé depuis le thread GUI — doit être rapide et ne PAS
 *        modifier l'état de l'app (utilise le mutex en lecture seule).
 */
void ttf_view_draw_callback(Canvas* canvas, void* context);

/**
 * @brief Callback de saisie clavier (boutons du Flipper).
 *        Envoie l'InputEvent dans l'event_queue du main thread.
 */
void ttf_view_input_callback(InputEvent* event, void* context);
