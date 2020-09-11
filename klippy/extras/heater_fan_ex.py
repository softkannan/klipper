# Support fans that are enabled when a heater is on
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import fan

PIN_MIN_TIME = 0.100

class PrinterHeaterFanEx:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.load_object(config, 'heaters')
        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.heater_name = config.get("heater", "extruder")
        self.heater_temp = config.getfloat("heater_temp", 50.0)
        self.heaters = []
        self.fan = fan.Fan(config, default_shutdown_speed=1.)
        self.fan_speed = config.getfloat("fan_speed", 1., minval=0., maxval=1.)
        self.stepper_names = []
        self.stepper_enable = self.printer.load_object(config, 'stepper_enable')
        self.idle_speed = config.getfloat('idle_speed', default=self.fan_speed, minval=0., maxval=1.)
        self.idle_timeout = config.getint("idle_timeout", default=30, minval=0)
        self.last_on = self.idle_timeout
    def handle_ready(self):
        pheaters = self.printer.lookup_object('heaters')
        self.heaters = [pheaters.lookup_heater(n.strip())
                        for n in self.heater_name.split(',')]
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        self.stepper_names = [s.get_name() for s in kin.get_steppers()]
        reactor = self.printer.get_reactor()
        reactor.register_timer(self.callback, reactor.NOW)
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def callback(self, eventtime):
        power = 0.
        active = False
        for heater in self.heaters:
            current_temp, target_temp = heater.get_temp(eventtime)
            if target_temp or current_temp > self.heater_temp:
                active = True
        for name in self.stepper_names:
            if self.stepper_enable.lookup_enable(name).is_motor_enabled():
                active = True
        if active:
            self.last_on = 0
            power = self.fan_speed
        elif self.last_on < self.idle_timeout:
            power = self.idle_speed
            self.last_on += 1
        print_time = self.fan.get_mcu().estimated_print_time(eventtime)
        self.fan.set_speed(print_time + PIN_MIN_TIME, power)
        return eventtime + 1.

def load_config_prefix(config):
    return PrinterHeaterFanEx(config)
