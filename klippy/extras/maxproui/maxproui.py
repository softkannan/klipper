# Support for Anycubic 4Max Pro touchscreens
#
# Copyright (C) 2020  Kannan K
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#TODO: filename handling issue, need to find away to generate short and long filenames and later mapping them to real name
import os, logging, struct, textwrap
import mcu
from .. import gcode_macro, heaters

ANYCUBIC_TFT_PAGE_SIZE = 4
ANYCUBIC_TFT_MAX_MSG_SIZE = 50
ANYCUBIC_TFT_MAX_FILENAME_SIZE = 26
ANYCUBIC_TFT_MAX_SHORT_FILENAME_SIZE = 13

#while print is inprogress user cannot access special menu
ANYCUBIC_TFT_MENU_SPECIAL_MENU = "<special_menu>" 
ANYCUBIC_TFT_MENU_EXIT = "<exit>"

class TFTMenuItem:
    def __init__(self, config):
        self.name = config.get('name', None)
        self.fullname = ("<%s>" % self.name).lower().strip()
        self.gcode = config.get('gcode', None)
        self.requestStayOnPage = config.get('requestStayOnPage', True)
        self.showLastPage = config.get('showLastPage', True)

class MAXPROUI:
    def __init__(self, config):
        #Printer object, top level object 
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        #gcode dispatch and gcode macro dispatch (self.gcode.run_script("M220 S%d" % (self.feedrate_splice * 100)))
        self.gcode = self.printer.lookup_object('gcode')
        # load printer objects
        self.gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.gcode_queue = []
        self.mcu = mcu.get_printer_mcu(self.printer,
                                       config.get('maxpro_mcu', 'mcu'))
        self.oid = self.mcu.create_oid()

        #update system info
        self.softversion = self.printer.get_start_args().get('software_version')

        #initialize the member variable
        self._maxproui_write=None
        # minimum delay between back to back messages
        self.cmd_delay = config.get('cmd_delay', 0.01)
        self._baud = config.getint('baud', 115200, minval=1200, maxval=921600)

        self._is_connected = False
        self.previous_state = 'standby'
        self.extruder_current_temp = 0
        self.extruder_target_temp = 0
        self.bed_current_temp = 0
        self.bed_target_temp = 0
        self.fan_speed = 0
        self.print_progress = 0
        self.print_time = ""
        
        self.toolhead = None
        self.fan = None
        self.bed = None
        self.heater = None
        self.display_status = self.load_object_internal(config, "display_status")
        self.sdcard = self.load_object_internal(config, "virtual_sdcard")
        self.pause_resume = self.load_object_internal(config,"pause_resume")
        # Print Stat Tracking
        self.print_stats = self.load_object_internal(config, 'print_stats')
        self.idle_timeout = self.load_object_internal(config, 'idle_timeout')
        self.gcode_move = None

        self.menu_table = []
        self.menu_table_lookup = {}
        self.selectedSDFile=""
        self.lastUserSelection = ""
        self.lastPageNo = 0
        self.specialMenuActive = False
        self.requestStayOnPage = False
        self.requestExeCmd = False
        self.showLastPage = False
        self.lastShownPageIdx = 0

        # Load items from main config
        self.load_menuitems(config)

        #Callback registrations
        #get called on mcu config phase
        self.mcu.register_config_callback(self.build_config)
        
        #get called on klippy state changes
         #self.printer.register_event_handler("klippy:connect", self._handle_connect)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)
        self.printer.register_event_handler("klippy:disconnect", self._handle_disconnect)
        self.gcode.register_command("TFTCMD", self.cmd_TFTCMD,
                                    desc=self.cmd_TFTCMD_help)

    def _handle_ready(self):
        
        pheaters = self.printer.lookup_object('heaters')
        self.heater = pheaters.lookup_heater('extruder')
        self.bed = pheaters.lookup_heater('heater_bed')

        self.fan = self.printer.lookup_object('fan')
        
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode_move = self.printer.lookup_object('gcode_move')

        self.reactor.register_timer(self.printer_stats_update_callback, self.reactor.NOW)
        self.start_ui_callback_timer = self.reactor.register_timer(self.start_ui_callback, self.reactor.NOW + 5)
        self._is_connected = True

    def _handle_shutdown(self):
        msg = getattr(self.mcu, "_shutdown_msg", "").strip()
        # if self._notification_sound >= 0:
        #     self.play_sound(self._notification_sound)

    def _handle_disconnect(self):
        self._is_connected = False
        self._current_page = ""

    def build_config(self):
        self.mcu.add_config_cmd(
            "config_maxproui oid=%d baud=%d"
            % (self.oid, self._baud))

        curtime = self.reactor.monotonic()
        self._last_cmd_time = self.mcu.estimated_print_time(curtime)

        cmd_queue = self.mcu.alloc_command_queue()
        self._maxproui_write = self.mcu.lookup_command(
                    "maxproui_write oid=%c completed=%c data=%*s", cq=cmd_queue)


        self.mcu.register_response(self._handle_maxproui_received,
                                   "maxproui_received")

    def maxproui_write(self, data, cmd_delay = -1, completed = 1):
        if not self._is_connected or self._maxproui_write is None:
            return
        if cmd_delay == -1:
            cmd_delay = self.cmd_delay
        logging.info("maxproui_response %s", data)
        curtime = self.reactor.monotonic()
        print_time = self.mcu.estimated_print_time(curtime)
        print_time = max(self._last_cmd_time + cmd_delay, print_time)
        clock = self.mcu.print_time_to_clock(print_time)
        self._maxproui_write.send([self.oid, completed, list(data)],
                                    minclock=clock)
        self._last_cmd_time = print_time

    def start_ui_callback(self, eventtime):
        self.reactor.unregister_timer(self.start_ui_callback_timer)
        self.maxproui_write("J17\r\n",0.010)
        self.maxproui_write("J12\r\n",0.010)
    
    def printer_stats_update_callback(self, eventtime):

        self.extruder_current_temp, self.extruder_target_temp = self.heater.get_temp(eventtime)
        self.bed_current_temp, self.bed_target_temp = self.bed.get_temp(eventtime)
        statusData = self.fan.get_status(eventtime)
        self.fan_speed = statusData['speed']
        statusData = self.display_status.get_status(eventtime)
        self.print_progress = statusData['progress'] * 100.0
        print_stat = self.print_stats.get_status(eventtime)
        state = print_stat['state']
        if 'total_duration' in print_stat:
            self.print_time = print_stat['total_duration']

        if state == 'standby' and state != self.previous_state:
            #Ready
            self.maxproui_write("J12\r\n")
        elif state == 'printing' and state != self.previous_state:
            #"Print from SD Card"
            self.maxproui_write("J04\r\n")
        elif state == 'paused' and state != self.previous_state:
            #"Command has been send waiting for response message" this must followed with "J18 / J16 / J14"
            self.maxproui_write('J05\r\n')
            #Brings Continue and Resume screen and waits for user input on screen
            self.maxproui_write("J18\r\n",0.250)
        elif state == 'cancelled' and state != self.previous_state:
            #Brings Print and Resume screen and waits for user input on screen
            self.maxproui_write("J14\r\n")
        elif state == 'complete' and state != self.previous_state:
            #Brings Print Complete Dialog box and waits for the user input
            self.maxproui_write("J14\r\n")
        elif state == 'error' and state != self.previous_state:
            #It Has An Emergency Stop
            self.maxproui_write("J14\r\n")
        else:
            pass

        self.previous_state = state
        #schedule next event
        return eventtime + 1.

    def load_object_internal(self,config, objectname):
        retVal = None
        try:
            retVal = self.printer.load_object(
                config, objectname)
        except config.error:
            raise self.printer.config_error(
                "4Max Pro TFT requires [%s] to work,"
                " please add it to your config!" % objectname)
        return retVal

    def queue_gcode(self, script):
        if not script:
            return
        if not self.gcode_queue:
            reactor = self.printer.get_reactor()
            reactor.register_callback(self.dispatch_gcode)
        logging.info(script)
        self.gcode_queue.append(script)

    def dispatch_gcode(self, eventtime):
        while self.gcode_queue:
            script = self.gcode_queue[0]
            try:
                self.gcode.run_script(script)
            except Exception:
                logging.exception("Script running error")
            self.gcode_queue.pop(0)

    def parse_tft_command(self,tftRawCommand):
        retVal = { 'cmd' : ""}
        key = "cmd"
        rawLen = len(tftRawCommand)
        if rawLen > 0 and tftRawCommand[0] == 'A':
            for element in tftRawCommand[1:]:
                if element.isspace():
                    pass
                elif element.isalpha() or element == '<':
                    key = element
                    retVal[key] = ""
                else:
                    retVal[key] += element
                
        return retVal

    def load_menuitems(self, config):
        for cfg in config.get_prefix_sections('tft_menu '):
            item = TFTMenuItem(cfg)
            if item.fullname in self.menu_table_lookup:
                logging.info(
                    "Declaration of '%s' hides "
                    "previous menu declaration" % item.fullname)
            self.menu_table.append(item)
            self.menu_table_lookup[item.fullname] = item

    def send_menu_list_tft(self,menuList):
        #To overcome anycubic TFT buffer size limit
        #send line by line, line lengh must be < 50
        # for stable function
        self.maxproui_write('FN \r\n', -1, 0)
        for menuItem in menuList:
            self.maxproui_write(menuItem, -1, 0)
        self.maxproui_write('END\r\n')
    
    #Method resposible for generating extra TFT menu items also
    #This method generates list of gcode files
    def build_special_file_list_menu(self, startIdx):

        #Execute command, in response to refresh button touch
        #We get follow up A13, use that to queue the gcode
        #This approch provide stable menu functon 
        if self.lastUserSelection.startswith('<') and self.requestExeCmd:
            if self.lastUserSelection in self.menu_table_lookup:
                self.queue_gcode(self.menu_table_lookup[self.lastUserSelection].gcode)
                self.requestStayOnPage = self.menu_table_lookup[self.lastUserSelection].requestStayOnPage
                self.showLastPage = self.menu_table_lookup[self.lastUserSelection].showLastPage
            else:
                self.requestStayOnPage = False
                self.showLastPage = False
            self.requestExeCmd = False

        sdList = []
        if self.specialMenuActive:
            #if special menu is active then generate special menu items
            if self.requestStayOnPage and self.showLastPage:
                startIdx = self.lastShownPageIdx
            else:
                self.lastShownPageIdx=startIdx
            self.showLastPage = False

            idx = startIdx
            max_files = 0
            dir_files = len(self.menu_table)

            # clip number of display items to ANYCUBIC_TFT_PAGE_SIZE
            if dir_files < ANYCUBIC_TFT_PAGE_SIZE:
                max_files = dir_files
            else:
                max_files = startIdx + ANYCUBIC_TFT_PAGE_SIZE
                if max_files > dir_files:
                    max_files = dir_files

            for idx in range(startIdx, max_files):
                sdList += ['%s\r\n' % self.menu_table[idx].fullname]
                sdList += ['%s\r\n' % self.menu_table[idx].name]

        else:
            #on root level generate the file list based on current page number
            idx=startIdx
            max_files=ANYCUBIC_TFT_PAGE_SIZE
            flist = self.sdcard.get_file_list()
            dir_files=len(flist)
            # clip number of display items to ANYCUBIC_TFT_PAGE_SIZE
            if dir_files < ANYCUBIC_TFT_PAGE_SIZE:
                max_files=dir_files + 1
            else:
                max_files=startIdx + ANYCUBIC_TFT_PAGE_SIZE
                if max_files > dir_files:
                    max_files=dir_files + 1
            for idx in range(startIdx, max_files):
                if idx == 0:
                    # Special Entry
                    # Only display special menu on root level + page number 0
                    # long path always comes out as lower case so care full while compare
                    sdList += ['<special_menu>\r\n']
                    sdList += ['Special Menu\r\n']
                else:
                    fullPath, size = flist[idx-1]
                    # make sure filaname contains only legal chars, _ / Alphanum values rest are not supported
                    shortPath = fullPath[:ANYCUBIC_TFT_MAX_SHORT_FILENAME_SIZE]
                    # Go back to one cnt to account special menu entry
                    sdList += ['%s\r\n' % fullPath[:ANYCUBIC_TFT_MAX_FILENAME_SIZE]]
                    sdList += ['%s\r\n' % shortPath]

        self.send_menu_list_tft(sdList)

    # this method get called when ever TFT request information or asks to command operation
    def _handle_maxproui_received(self, params):
        
        if not self._is_connected:
            return
        #get the pay load, refer sendf for why using data is index
        data = params['data']

        logging.info("maxproui_received %s %s", params, data)
        
        # Parse the data received from the TFT display
        cmdData = self.parse_tft_command(data)
        cmdStr = cmdData['cmd']
        if len(cmdStr) == 0:
            return
        
        cmd = int(cmdStr)
       
        if cmd == 0:
            #A0 GET HOTEND TEMP
            self.maxproui_write("A0V %d\r\n" % self.extruder_current_temp)
        elif cmd == 1:
            #A1  GET HOTEND TARGET TEMP
            self.maxproui_write("A1V %d\r\n" % self.extruder_target_temp)
        elif cmd == 2:
            #A2 GET HOTBED TEMP
            self.maxproui_write("A2V %d\r\n" % self.bed_current_temp)
        elif cmd == 3:
            #A3 GET HOTBED TARGET TEMP
            self.maxproui_write("A3V %d\r\n" % self.bed_target_temp)
        elif cmd == 4:
            #A4 GET FAN SPEED
            self.maxproui_write("A4V %d\r\n" % self.fan_speed)
        elif cmd == 5:
            #A5 GET CURRENT COORDINATE
            x, y, z, e = self.toolhead.get_position()
            self.maxproui_write("A5V X: %.1f Y: %.1f Z: %.1f \r\n" % (x, y, z))
        elif cmd == 6:
            #A6 GET SD CARD PRINTING STATUS
            self.maxproui_write("A6V %d\r\n" % self.print_progress)
        elif cmd == 7:
            #A7 GET PRINTING TIME / TOTAL DURATION
            if self.print_time > 0:
                self.maxproui_write("A7V %02d H %02d M\r\n" % (self.print_time // (60 * 60), (self.print_time // 60) % 60))
            else:
                self.maxproui_write("A7V 999:999\r\n")
        elif cmd == 8:
            #A8 GET SD LIST (at launch / back button press / down arrow / up arrow button press / after selection refresh button press)
            pageNo = 0
            if 'S' in cmdData:
                pageNo = int(cmdData['S'])
            self.build_special_file_list_menu(pageNo)
        elif cmd == 9:
            #A9 pause sd print
            self.queue_gcode("PAUSE")
            self.maxproui_write("\r\n")
        elif cmd == 10:
            #A10 resume sd print
            self.queue_gcode("RESUME")
            self.maxproui_write("\r\n")
        elif cmd == 11:
            #A11 STOP SD PRINT
            self.queue_gcode("CANCEL_PRINT")
            self.maxproui_write("\r\n")
        elif cmd == 12:
            #A12 kill
            self.queue_gcode("M112")
            self.maxproui_write("J11\r\n")
        elif cmd == 13:
            #A13 SELECTION FILE (By touching in touch pad / user makes selection by touching item in screen)
            self.lastUserSelection = ""
            self.selectedSDFile = ""
            curSelection = data.replace('A13','',1).strip()
            logging.info("Current Selection : %s" % curSelection)
            if curSelection.startswith('<'):
                self.lastUserSelection = curSelection
                if self.lastUserSelection == ANYCUBIC_TFT_MENU_SPECIAL_MENU:
                    self.specialMenuActive = True
                    self.requestStayOnPage = False
                    self.showLastPage = False
                    self.lastShownPageIdx = 0
                    self.lastUserSelection = ""
                elif self.lastUserSelection == ANYCUBIC_TFT_MENU_EXIT:
                    self.specialMenuActive = False
                    self.requestStayOnPage = False
                    self.showLastPage = False
                    self.lastShownPageIdx = 0
                    self.lastUserSelection = ""
                else:
                    pass
                #J21 Open failed, Switch Screen to File Browser: Print not avalible
                #Disables the Print and Resume button on file explorer
                self.maxproui_write("J21\r\n")
            else:
                self.selectedSDFile = curSelection
                #J20 Open successful, Switch Screen to File Browser: Print avalible
                #Enables / Highlights Print and Resume button on explorer
                self.maxproui_write("J20\r\n")
        elif cmd == 14:
             #A14 START PRINTING
            if len(self.selectedSDFile) > 0:
                self.queue_gcode("SDCARD_PRINT_FILE FILENAME=%s" % self.selectedSDFile)
            self.maxproui_write("\r\n")
        elif cmd == 15:
            #A15 RESUMING FROM OUTAGE / After file is selected then user hits resume button instead of print button
            if len(self.selectedSDFile) > 0:
                self.queue_gcode("SDCARD_PRINT_FILE FILENAME=%s" % self.selectedSDFile)
            self.maxproui_write("\r\n")
        elif cmd == 16:
            #A16 set hotend temp
            if 'S' in cmdData:
                self.queue_gcode("M104 S%s" % cmdData['S'])
            elif 'C' in cmdData:
                x, y, z, e = self.toolhead.get_position()
                if z < 10.0:
                    self.queue_gcode("G1 Z10\nM104 S%s" % cmdData['C'])
                else:
                    self.queue_gcode("M104 S%s" % cmdData['C'])
            self.maxproui_write("\r\n")
        elif cmd == 17:
            #A17 set heated bed temp
            if 'S' in cmdData:
                self.queue_gcode("M140 S%s" % cmdData['S'])
            self.maxproui_write("\r\n")
        elif cmd == 18:
            #A18 set fan speed
            if 'S' in cmdData:
                fanspeed_val = (int(cmdData['S']) / 100.0) * 255
                self.queue_gcode("M106 S%d" % fanspeed_val)
            else:
                self.queue_gcode("M106 S255")
            self.maxproui_write("\r\n")
        elif cmd == 19:
            #A19 stop stepper drivers
            self.queue_gcode("M18\nM84")
            self.maxproui_write("\r\n")
        elif cmd == 20:
            #A20 read printing speed
            if 'S' in cmdData:
                self.queue_gcode("M220 S%d" % cmdData['S'])
                self.maxproui_write("\r\n")
            else:
                self.maxproui_write("A20V %d\r\n" % (self.gcode_move.speed_factor * 60.0 * 100.0))
        elif cmd == 21:
            #A21 all home
            if 'X' in cmdData:
                self.queue_gcode("G28 X")
            elif 'Y' in cmdData:
                self.queue_gcode("G28 Y")
            elif 'Z' in cmdData:
                self.queue_gcode("G28 Z")
            else:
                self.queue_gcode("G28")
            self.maxproui_write("\r\n")
        elif cmd == 22:
            #A22 move X/Y/Z or extrude
            if 'X' in cmdData:
                if 'F' in cmdData:
                    self.queue_gcode("G91\nG1 X%sF%s\nG90" % (cmdData['X'],cmdData['F']))
                else:
                    self.queue_gcode("G91\nG1 X%s\nG90" % cmdData['X'])
            if 'Y' in cmdData:
                if 'F' in cmdData:
                    self.queue_gcode("G91\nG1 Y%sF%s\nG90" % (cmdData['Y'],cmdData['F']))
                else:
                    self.queue_gcode("G91\nG1 Y%s\nG90" % cmdData['Y'])
            if 'Z' in cmdData:
                if 'F' in cmdData:
                    self.queue_gcode("G91\nG1 Z%sF%s\nG90" % (cmdData['Z'],cmdData['F']))
                else:
                    self.queue_gcode("G91\nG1 Z%s\nG90" % cmdData['Z'])
            if 'E' in cmdData:
                if 'F' in cmdData:
                    self.queue_gcode("G91\nG1 E%sF%s\nG90" % (cmdData['E'],cmdData['F']))
                else:
                    self.queue_gcode("G91\nG1 E%s\nG90" % cmdData['E'])
            self.maxproui_write("\r\n")
        elif cmd == 23:
            #A23 preheat pla
            x, y, z, e = self.toolhead.get_position()
            if z < 10.0:
                self.queue_gcode("G1 Z10")
                self.queue_gcode("M104 S200\nM140 S55")
            else:
                self.queue_gcode("M104 S200\nM140 S55")
            self.maxproui_write("\r\n")
        elif cmd == 24:
            #A24 preheat abs
            x, y, z, e = self.toolhead.get_position()
            if z < 10.0:
                self.queue_gcode("G1 Z10")
                self.queue_gcode("M104 S230\nM140 S75")
            else:
                self.queue_gcode("M104 S230\nM140 S75")
            self.maxproui_write("\r\n")
        elif cmd == 25:
            #A25 cool down
            self.queue_gcode("M104 S0\nM140 S0\nG1 Z10")
            self.maxproui_write("\r\n")
        elif cmd == 26:
            #A26 refresh SD (user touch refresh key), always TFT display will issue A8S command to get new list after refresh button press
            #On special menu case it will act like enter button
            if self.lastUserSelection.startswith('<'):
                self.requestExeCmd = True
            self.maxproui_write("\r\n")
        elif cmd == 27:
            #A27 servos angles  adjust
            self.maxproui_write("\r\n")
        elif cmd == 28:
            #A28 filament test
            self.maxproui_write("\r\n")
        elif cmd == 29:
            #A29 Z PROBE OFFESET SET
            self.maxproui_write("\r\n")
        elif cmd == 30:
            #A30 assist leveling, the original function was canceled
            self.maxproui_write("\r\n")
        elif cmd == 31:
            #A31 zoffset
            self.maxproui_write("\r\n")
        elif cmd == 32:
            #A32 clean leveling beep flag
            self.maxproui_write("\r\n")
        elif cmd == 33:
            #A33 get version info
            self.maxproui_write("J33 %s\r\n" % self.softversion)
        elif cmd == 40:
            #A40 reset mainboard
            self.queue_gcode("FIRMWARE_RESTART")
            self.maxproui_write("\r\n")
        elif cmd == 41:
            #A41 continue button pressed
            powerOff = False
            if 'O' in cmdData:
                powerOff = True
            elif 'C' in cmdData:
                powerOff = False
            elif 'S' in cmdData:
                if powerOff:
                    self.maxproui_write("J35 \r\n")
                else:
                    self.maxproui_write("J34 \r\n")
        elif cmd == 42:
            #A42 case light button pressed
            if 'O' in cmdData:
                self.queue_gcode("LEDMAX")
            else:
                self.queue_gcode("LEDOFF")
            self.maxproui_write("\r\n")
    
    cmd_TFTCMD_help = ("Sends 4MAXPro TFT Command")
    def cmd_TFTCMD(self, gcmd):
        tft_cmd = gcmd.get("CMD")
        self.maxproui_write("%s\r\n" % tft_cmd)
    



def load_config(config):
    return MAXPROUI(config)
