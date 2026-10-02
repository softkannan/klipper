#ifndef __GENERIC_MAXPROUI_SERIAL_IRQ_H
#define __GENERIC_MAXPROUI_SERIAL_IRQ_H

#include <stdint.h> // uint32_t

void maxproui_init(uint32_t baud);
// callback provided by board specific code
void maxproui_enable_tx_irq(void);

// serial_irq.c
void maxproui_rx_byte(uint_fast8_t data);
int maxproui_get_tx_byte(uint8_t *pdata);

#endif // serial_irq.h
