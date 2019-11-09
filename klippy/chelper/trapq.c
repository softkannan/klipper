// Trapezoidal velocity movement queue
//
// Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <math.h> // sqrt
#include <stddef.h> // offsetof
#include <stdlib.h> // malloc
#include <string.h> // memset
#include "compiler.h" // unlikely
#include "trapq.h" // move_get_coord

// Allocate a new 'move' object
struct move *
move_alloc(void)
{
    struct move *m = malloc(sizeof(*m));
    memset(m, 0, sizeof(*m));
    return m;
}

// Fill and add a move to the trapezoid velocity queue
void __visible
trapq_append(struct trapq *tq, double print_time
             , double accel_t, double cruise_t, double decel_t
             , double start_pos_x, double start_pos_y, double start_pos_z
             , double axes_r_x, double axes_r_y, double axes_r_z
             , double start_v, double cruise_v, double accel)
{
    struct coord start_pos = { .x=start_pos_x, .y=start_pos_y, .z=start_pos_z };
    struct coord axes_r = { .x=axes_r_x, .y=axes_r_y, .z=axes_r_z };
    if (accel_t) {
        struct move *m = move_alloc();
        m->print_time = print_time;
        m->move_t = accel_t;
        m->start_v = start_v;
        m->half_accel = .5 * accel;
        m->start_pos = start_pos;
        m->axes_r = axes_r;
        trapq_add_move(tq, m);

        print_time += accel_t;
        start_pos = move_get_coord(m, accel_t);
    }
    if (cruise_t) {
        struct move *m = move_alloc();
        m->print_time = print_time;
        m->move_t = cruise_t;
        m->start_v = cruise_v;
        m->half_accel = 0.;
        m->start_pos = start_pos;
        m->axes_r = axes_r;
        trapq_add_move(tq, m);

        print_time += cruise_t;
        start_pos = move_get_coord(m, cruise_t);
    }
    if (decel_t) {
        struct move *m = move_alloc();
        m->print_time = print_time;
        m->move_t = decel_t;
        m->start_v = cruise_v;
        m->half_accel = -.5 * accel;
        m->start_pos = start_pos;
        m->axes_r = axes_r;
        trapq_add_move(tq, m);
    }
}

// Return the distance moved given a time in a move
inline double
move_get_distance(struct move *m, double move_time)
{
    return (m->start_v + m->half_accel * move_time) * move_time;
}

// Return the XYZ coordinates given a time in a move
inline struct coord
move_get_coord(struct move *m, double move_time)
{
    double move_dist = move_get_distance(m, move_time);
    return (struct coord) {
        .x = m->start_pos.x + m->axes_r.x * move_dist,
        .y = m->start_pos.y + m->axes_r.y * move_dist,
        .z = m->start_pos.z + m->axes_r.z * move_dist };
}

// Allocate a new 'trapq' object
struct trapq * __visible
trapq_alloc(void)
{
    struct trapq *tq = malloc(sizeof(*tq));
    memset(tq, 0, sizeof(*tq));
    list_init(&tq->moves);
    return tq;
}

// Free memory associated with a 'trapq' object
void __visible
trapq_free(struct trapq *tq)
{
    while (!list_empty(&tq->moves)) {
        struct move *m = list_first_entry(&tq->moves, struct move, node);
        list_del(&m->node);
        free(m);
    }
    free(tq);
}

// Add a move to the trapezoid velocity queue
void
trapq_add_move(struct trapq *tq, struct move *m)
{
    list_add_tail(&m->node, &tq->moves);
}

// Free any moves older than `print_time` from the trapezoid velocity queue
void __visible
trapq_free_moves(struct trapq *tq, double print_time)
{
    while (!list_empty(&tq->moves)) {
        struct move *m = list_first_entry(&tq->moves, struct move, node);
        if (m->print_time + m->move_t > print_time)
            return;
        list_del(&m->node);
        free(m);
    }
}
