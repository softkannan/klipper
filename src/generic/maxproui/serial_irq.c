// Generic interrupt based serial uart helper code
//
// Copyright (C) 2016-2018  Kannan K
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <string.h> // memmove
#include "basecmd.h" // oid_alloc
#include "board/io.h" // readb
#include "board/irq.h" // irq_save
#include "board/misc.h" // timer_read_time
#include "board/maxproui/serial_irq.h" // maxproui_enable_tx_irq
#include "command.h" // DECL_CONSTANT
#include "sched.h" // sched_wake_task

#define RECEIVE_BUFF_SIZE                   96 //Anycubic 4Max Pro TFT max buffer
#define TRANSMIT_BUFF_SIZE                  96 //Anycubic 4Max Pro TFT max buffer

struct serial_display_ui_s {
    struct timer timer;
    uint32_t baud;
};

//Data Buffers
static uint8_t tft_receive_buf[RECEIVE_BUFF_SIZE];
static uint8_t tft_receive_pos = 0;
static uint8_t tft_dataCount = 0;
static uint8_t tft_receivNewMsg = 1;

static uint8_t tft_transmit_buf[TRANSMIT_BUFF_SIZE];
static uint8_t tft_transmit_pos = 0;
static uint8_t tft_transmit_max = 0;

//simple flag variable read by maxproui_process_receive_data_task, 
//this flag will be set by RX interrupt 
static struct task_wake maxproui_wake;

void
maxproui_send_command(uint8_t *data, uint_fast8_t data_len)
{
    if (data_len < 1)
        return;

    // Verify space for message
    uint_fast8_t tpos = readb(&tft_transmit_pos);
    uint_fast8_t tmax = readb(&tft_transmit_max);
    if (tpos >= tmax) {
        tpos = tmax = 0;
        writeb(&tft_transmit_max, 0);
        writeb(&tft_transmit_pos, 0);
    }
    uint_fast8_t msglen = data_len;
    if (tmax + msglen > sizeof(tft_transmit_buf)) {
        if (tmax + msglen - tpos > sizeof(tft_transmit_buf))
            // Not enough space for message
            return;
        // Disable TX irq and move buffer
        writeb(&tft_transmit_max, 0);
        tpos = readb(&tft_transmit_pos);
        tmax -= tpos;
        memmove(&tft_transmit_buf[0], &tft_transmit_buf[tpos], tmax);
        writeb(&tft_transmit_pos, 0);
        writeb(&tft_transmit_max, tmax);
        maxproui_enable_tx_irq();
    }

    // Generate message
    memcpy(&tft_transmit_buf[tmax], data, data_len);

    // Start message transmit
    writeb(&tft_transmit_max, tmax + msglen);
    maxproui_enable_tx_irq();
}


void
command_config_maxproui(uint32_t *args)
{
    uint8_t timeout_data_len = args[4];
    struct serial_display_ui_s *t = oid_alloc(
        args[0], command_config_maxproui, sizeof(*t) + timeout_data_len);
    t->baud = args[1];  
    maxproui_init(t->baud);
}
DECL_COMMAND(command_config_maxproui,
             "config_maxproui oid=%c baud=%u");

void
command_maxproui_write(uint32_t *args)
{
    //Needed only for stateful command, in our case state is stored in the host
    //struct serial_display_ui_s *t = oid_lookup(args[0], command_config_maxproui);
    uint_fast8_t completed = args[1];
    uint_fast8_t data_len = args[2];
    uint8_t *data = (void*)(size_t)args[3];
    maxproui_send_command(data, data_len);
    //at the end of response transmit to TFT enable the receive,
    //which will start receiving the data, tried circular buffer 
    //but the TFT is not liking, so one receive and matching transmit
    //then receive works better and must more stable. so workaround 
    if(completed == 1)
    {
        writeb(&tft_receivNewMsg,1);
    }
}
DECL_COMMAND_FLAGS(command_maxproui_write, HF_IN_SHUTDOWN,
                   "maxproui_write oid=%c completed=%c data=%*s");

// Rx interrupt - store read data
void
maxproui_rx_byte(uint_fast8_t data)
{
    //Anycubic TFT request always ends with new line character.
    if( data == '\n' ||
        data == '\r' ||
        data == ':'  ||
        tft_receive_pos > RECEIVE_BUFF_SIZE
    )
    {
        if(!tft_receive_pos)
        {
            return;
        }
        //Terminate the string
        tft_receive_buf[tft_receive_pos] = 0;
        writeb(&tft_dataCount,tft_receive_pos);
        tft_receive_pos = 0;
        //Stop receiving further message until we send response to 
        //already received message
        writeb(&tft_receivNewMsg,0);
         //send command to host
        sched_wake_task(&maxproui_wake);
    }
    else
    {
        //Truncate any string larger than buffer size / already system experiences the buffer overflow
        if(readb(&tft_receivNewMsg))
        {
            tft_receive_buf[tft_receive_pos++] = data;
        }
    }
}

// Tx interrupt - get next byte to transmit
int
maxproui_get_tx_byte(uint8_t *pdata)
{
    if (tft_transmit_pos >= tft_transmit_max)
        return -1;
    *pdata = tft_transmit_buf[tft_transmit_pos++];
    return 0;
}

// Process any incoming commands
void
maxproui_process_receive_data_task(void)
{
    // Check for the wake flag and return
    if (!sched_check_wake(&maxproui_wake))
        return;
     
    uint_fast8_t data_len = readb(&tft_dataCount);
    if (data_len > 0) {
        uint8_t *data = (uint8_t*)&tft_receive_buf[0];
        sendf("maxproui_received data=%*s", data_len, data);
    }
}
//Enqueue the task function to global task table 
//which will execute everytime task loop is called
DECL_TASK(maxproui_process_receive_data_task);
