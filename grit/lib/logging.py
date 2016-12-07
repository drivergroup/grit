"""
Copyright (c) 2011-2015 Nathan Boley

This file is part of GRIT.

GRIT is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

GRIT is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with GRIT.  If not, see <http://www.gnu.org/licenses/>.
"""

import os, sys
import time
import curses
import multiprocessing

import grit

from grit import config

MAX_REFRESH_TIME = 1e-2
MAX_NCOL = 120
N_LOG_ROWS = 10

def manage_curses_display(stdscr, msg_queue, msg_queue_lock, nthreads=1):
    curses.curs_set(0)
    base_pad = curses.newpad(1000, 500)
    base_pad.timeout(0)
    header = base_pad.subpad(2, MAX_NCOL, 1, 1)

    thread_data_windows = []
    thread_data_windows.append( base_pad.subpad(1, MAX_NCOL, 3, 1) )
    thread_data_windows[-1].insstr( 0, 0, "Thread 0:".ljust(11) )
    for i in range(nthreads):
        thread_data_windows.append( base_pad.subpad(1, MAX_NCOL, 3+i+1, 1))
        thread_data_windows[i+1].insstr(0, 0, ("Thread %i:" % (i+1)).ljust(11))

    base_pad.addstr(nthreads+2+2+1, 1, "Log:" )

    nrow, ncol = stdscr.getmaxyx()
    log_border = base_pad.subpad(
        N_LOG_ROWS+2, min(ncol, MAX_NCOL), nthreads+2+2+2, 1)
    log_border.border()
    log = log_border.subpad( N_LOG_ROWS, min(ncol, MAX_NCOL)-2, 1, 1)

    header.addstr(0, 0, "GRIT (version %s)" % grit.__version__ )
    
    while True:
        start_time = time.time()
        while True:
            # make sure that we refresh at least every 10 messages
            counter = 0
            
            # make sure that we refresh every one in a while
            if start_time - time.time() > MAX_REFRESH_TIME:
                break

            # try to acquire a message. If none exists, brak
            # to refresh and sleep
            try:
                thread_index, do_log, msg = msg_queue.pop()
            except IndexError as inst:
                break
            except IOError as inst:
                break
            
            # if the message is BREAK, then we are done so exit the thread
            if msg == 'BREAK': 
                return

            if do_log:
                log.insertln()
                log.insstr( msg )
            
            # truncate the message so that it doesnt extend past 80 charcters
            msg = msg[:MAX_NCOL-11]
            if thread_index != None:
                line = ("Thread %i:" % (thread_index)).ljust(11) \
                    + msg.ljust(MAX_NCOL-11)
                thread_data_windows[thread_index].erase()
                thread_data_windows[thread_index].insstr(0, 0, line )
            
            counter += 1
            if counter >= 10: break
        
        nrow, ncol = stdscr.getmaxyx()
        try:
            base_pad.refresh(0, 0, 0, 0, max(nrow-1,0), max(ncol-1,0))
        except:
            raise
    
    return

class Logger( object ):
    def _init_ncurses_manager(self):
        self.manager = multiprocessing.Manager()
        self.msgs_lock = multiprocessing.Lock()
        self.msgs = self.manager.list()
        p = multiprocessing.Process( target=curses.wrapper, 
                     args=(manage_curses_display, 
                           self.msgs, self.msgs_lock, self.nthreads) )
        p.start()
        self.curses_p = p
        self.main_pid = os.getpid()

        self.pid_to_index_mapping = self.manager.list()
        self.pid_to_index_mapping.append( self.main_pid )
        for loop in range(self.nthreads):
            self.pid_to_index_mapping.append( None )


    def __init__(self, nthreads, use_ncurses=False, log_ofstream=None):
        self.use_ncurses = use_ncurses
        self.nthreads = nthreads
        self.log_ofstream = log_ofstream
        
        if self.use_ncurses:
            self._init_ncurses_manager()
        
        return
    
    def __call__( self, message, display=True, log=False ):
        message = str(message)
        if not self.use_ncurses:
            if log or (config.DEBUG_VERBOSE and message.strip() != ''): 
                self.log_ofstream.write(message.strip() + "\n" )
                self.log_ofstream.flush()
                sys.stderr.write(message.strip() + "\n" )
            return

        # if the message is empty, always display and never log
        if message == "": 
            display = True
            log = False
        # if we want to log this, and we have an output stream, write this
        # to the log
        if (log or config.DEBUG_VERBOSE) and self.log_ofstream != None:
            self.log_ofstream.write(message.strip() + "\n" )
            self.log_ofstream.flush()
        
        # if we're not using ncurses, then write the message to standard err
        if display and not self.use_ncurses:
            sys.stderr.write(message.strip() + "\n" )
        
        # if we are using ncurses, and this is a message to display, then add
        # it to the display queue
        if display and self.use_ncurses:
            with self.msgs_lock:
                try: 
                    p_index = self.pid_to_index_mapping.index( os.getpid() )
                except ValueError:
                    try: 
                        p_index = min( i for i, pid in enumerate(self.pid_to_index_mapping) 
                                       if pid == None or not os.path.exists("/proc/%i"%pid))
                        self.pid_to_index_mapping[p_index] = os.getpid()
                    except:
                        #raise
                        return

                # only log message from main
                self.msgs.append( (p_index, log, message) )
        
        time.sleep(1e-2)
    
    def close(self):
        if self.use_ncurses:
            self.msgs.append( (None, False, 'BREAK') )
            self.curses_p.join()
