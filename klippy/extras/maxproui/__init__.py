# Package definition for the extras/display directory
#
# Copyright (C) 2018  Kannan K <softkannan@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from . import maxproui

def load_config(config):
    return maxproui.load_config(config)
