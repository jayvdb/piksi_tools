#!/usr/bin/env python
# Copyright (C) 2011-2014 Swift Navigation Inc.
# Contact: Fergus Noble <fergus@swift-nav.com>
#
# This source is subject to the license found in the file 'LICENSE' which must
# be be distributed together with this source. All other rights reserved.
#
# THIS CODE AND INFORMATION IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND,
# EITHER EXPRESSED OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND/OR FITNESS FOR A PARTICULAR PURPOSE.

from traits.api import Instance, Dict, HasTraits, Array, Float, on_trait_change, List, Int, Button, Bool, Str, File
from traitsui.api import Item, View, HGroup, VGroup, ArrayEditor, HSplit, TextEditor, TabularEditor, UItem, Tabbed
from traitsui.tabular_adapter import TabularAdapter
from chaco.api import ArrayPlotData, Plot
from chaco.tools.api import ZoomTool, PanTool
from enable.api import ComponentEditor
from enable.savage.trait_defs.ui.svg_button import SVGButton
from pyface.api import GUI
from piksi_tools.console.utils import plot_square_axes, determine_path, MultilineTextEditor, get_mode, mode_dict, color_dict,\
                                      EMPTY_STR, SPP_MODE, FLOAT_MODE, DGNSS_MODE, FIXED_MODE

import math
import os
import numpy as np
import datetime
import time

from sbp.navigation import *

class SimpleAdapter(TabularAdapter):
  columns = [('Item', 0), ('Value',  1)]
  width = 80

class SolutionView(HasTraits):
  python_console_cmds = Dict()
  # we need to doubleup on Lists to store the psuedo absolutes separately
  # without rewriting everything

  """
  logging_v : toggle logging for velocity files
  directory_name_v : location and name of velocity files
  logging_p : toggle logging for position files
  directory_name_p : location and name of velocity files
  """
  plot_history_max = Int(1000)
  logging_v = Bool(False)
  directory_name_v = File

  logging_p = Bool(False)
  directory_name_p = File

  lats_psuedo_abs = List()
  lngs_psuedo_abs = List()
  alts_psuedo_abs = List()

  table = List()
  dops_table = List()
  pos_table = List()
  vel_table = List()

  rtk_pos_note = Str("It is necessary to enter the \"Surveyed Position\" settings for the base station in order to view the RTK Positions in this tab.")

  plot = Instance(Plot)
  plot_data = Instance(ArrayPlotData)
  # Store plots we care about for legend

  running = Bool(True)
  zoomall = Bool(False)
  position_centered = Bool(False)

  clear_button = SVGButton(
    label='', tooltip='Clear',
    filename=os.path.join(determine_path(), 'images', 'iconic', 'x.svg'),
    width=16, height=16
  )
  zoomall_button = SVGButton(
    label='', tooltip='Zoom All', toggle=True,
    filename=os.path.join(determine_path(), 'images', 'iconic', 'fullscreen.svg'),
    width=16, height=16
  )
  center_button = SVGButton(
    label='', tooltip='Center on Solution', toggle=True,
    filename=os.path.join(determine_path(), 'images', 'iconic', 'target.svg'),
    width=16, height=16
  )
  paused_button = SVGButton(
    label='', tooltip='Pause', toggle_tooltip='Run', toggle=True,
    filename=os.path.join(determine_path(), 'images', 'iconic', 'pause.svg'),
    toggle_filename=os.path.join(determine_path(), 'images', 'iconic', 'play.svg'),
    width=16, height=16
  )

  traits_view = View(
    HSplit(
        VGroup(
          Item('', label='GNSS Solution', emphasized=True),
          Item('table', style='readonly',
                editor=TabularEditor(adapter=SimpleAdapter()),
                show_label=False, width=0.3),
          Item('rtk_pos_note', show_label=False, resizable=True,
            editor=MultilineTextEditor(TextEditor(multi_line=True)), 
            style='readonly', width=0.3, height=-40),
          ),
      VGroup(
        HGroup(
          Item('paused_button', show_label=False),
          Item('clear_button', show_label=False),
          Item('zoomall_button', show_label=False),
          Item('center_button', show_label=False),
        ),
        Item('plot',
          show_label=False,
          editor=ComponentEditor(bgcolor=(0.8,0.8,0.8))),
      )
    )
  )

  def _zoomall_button_fired(self):
    self.zoomall = not self.zoomall

  def _center_button_fired(self):
    self.position_centered = not self.position_centered

  def _paused_button_fired(self):
    self.running = not self.running

  def _clear_button_fired(self):
    self.tows = []
    self.lats = []
    self.lngs = []
    self.alts = []
    self.modes = []
    self.plot_data.set_data('lat_spp', [])
    self.plot_data.set_data('lng_spp', [])
    self.plot_data.set_data('alt_spp', [])
    self.plot_data.set_data('lat_dgnss', [])
    self.plot_data.set_data('lng_dgnss', [])
    self.plot_data.set_data('alt_dgnss', [])
    self.plot_data.set_data('lat_float', [])
    self.plot_data.set_data('lng_float', [])
    self.plot_data.set_data('alt_float', [])
    self.plot_data.set_data('lat_fixed', [])
    self.plot_data.set_data('lng_fixed', [])
    self.plot_data.set_data('alt_fixed', [])

  def _pos_llh_callback(self, sbp_msg, **metadata):
    # Updating an ArrayPlotData isn't thread safe (see chaco issue #9), so
    # actually perform the update in the UI thread.
    if self.running:
      GUI.invoke_later(self.pos_llh_callback, sbp_msg)

  def update_table(self):
    self._table_list = self.table_spp.items()

  def auto_survey(self):
    if self.counter < 1000:
      self.counter = self.counter + 1
    self.latitude_list.append(self.last_soln.lat)
    self.longitude_list.append(self.last_soln.lon)
    self.altitude_list.append(self.last_soln.height)
    self.latitude_list = self.latitude_list[-1000:]
    self.longitude_list = self.longitude_list[-1000:]
    self.altitude_list = self.altitude_list[-1000:]
    self.latitude = (sum(self.latitude_list))/self.counter
    self.altitude = (sum(self.altitude_list))/self.counter
    self.longitude = (sum(self.longitude_list))/self.counter

  def pos_llh_callback(self, sbp_msg, **metadata):
    self.last_stime_update = time.time()
    soln = MsgPosLLHDepA(sbp_msg)
    self.last_soln = soln

    masked_flag = get_mode(soln)
    pos_table = []

    tow = soln.tow * 1e-3
    if self.nsec is not None:
      tow += self.nsec * 1e-9

    if self.week is not None:
      t = datetime.datetime(1980, 1, 6) + \
          datetime.timedelta(weeks=self.week) + \
          datetime.timedelta(seconds=tow)
     
      if(self.directory_name_p == ''):
        filepath_p = time.strftime("position_log_%Y%m%d-%H%M%S.csv")
      else:
        filepath_p = os.path.join(self.directory_name_p, time.strftime("position_log_%Y%m%d-%H%M%S.csv"))

      if self.logging_p ==  False:
        self.log_file = None

      if self.logging_p:
        if self.log_file is None:
          self.log_file = open(filepath_p, 'w')
          self.log_file.write("time,latitude(degrees),longitude(degrees),altitude(meters),n_signals,flags\n")

        self.log_file.write('%s,%.10f,%.10f,%.4f,%d,%d\n' % (
          str(t),
          soln.lat, soln.lon, soln.height,
          soln.n_sats, soln.flags)
        )
        self.log_file.flush()

    if masked_flag != 0:
      pos_table.append(('GPS Time', t))
      pos_table.append(('GPS Week', str(self.week)))
      pos_table.append(('GPS ToW', tow))
      pos_table.append(('Num. Signals', soln.n_sats))
      pos_table.append(('Lat', soln.lat))
      pos_table.append(('Lng', soln.lon))
      pos_table.append(('Alt', soln.height))
      pos_table.append(('Flags', '0x%02x' % soln.flags))
      pos_table.append(('Mode', mode_dict[masked_flag]))
    else:
      pos_table.append(('GPS Time', EMPTY_STR))
      pos_table.append(('GPS Week', EMPTY_STR))
      pos_table.append(('GPS ToW', EMPTY_STR))
      pos_table.append(('Num. Signals', EMPTY_STR))
      pos_table.append(('Lat', EMPTY_STR))
      pos_table.append(('Lng', EMPTY_STR))
      pos_table.append(('Alt', EMPTY_STR))
      pos_table.append(('Flags', EMPTY_STR))
      pos_table.append(('Mode', EMPTY_STR))

    self.auto_survey()

    # setup_plot variables
    self.lats[1:] = self.lats[:-1]
    self.lngs[1:] = self.lngs[:-1]
    self.alts[1:] = self.alts[:-1]
    self.tows[1:] = self.tows[:-1]
    self.modes[1:] = self.modes[:-1]

    self.lats[0] = soln.lat
    self.lngs[0] = soln.lon
    self.alts[0] = soln.height
    self.tows[0] = soln.tow
    self.modes[0] = masked_flag

    self.lats = self.lats[-self.plot_history_max:]
    self.lngs = self.lngs[-self.plot_history_max:]
    self.alts = self.alts[-self.plot_history_max:]
    self.tows = self.tows[-self.plot_history_max:]
    self.modes = self.modes[-self.plot_history_max:]

    # SPP
    spp_indexer = (self.modes == SPP_MODE)
    dgps_indexer = (self.modes == DGNSS_MODE)
    float_indexer = (self.modes == FLOAT_MODE)
    fixed_indexer = (self.modes == FIXED_MODE)

    self.plot_data.set_data('lat_spp', self.lats[spp_indexer])
    self.plot_data.set_data('lng_spp', self.lngs[spp_indexer])
    self.plot_data.set_data('alt_spp', self.alts[spp_indexer])
    self.plot_data.set_data('lat_dgps', self.lats[dgps_indexer])
    self.plot_data.set_data('lng_dgps', self.lngs[dgps_indexer])
    self.plot_data.set_data('alt_dgps', self.alts[dgps_indexer])
    self.plot_data.set_data('lat_float', self.lats[float_indexer])
    self.plot_data.set_data('lng_float', self.lngs[float_indexer])
    self.plot_data.set_data('alt_float', self.alts[float_indexer])
    self.plot_data.set_data('lat_fixed', self.lats[fixed_indexer])
    self.plot_data.set_data('lng_fixed', self.lngs[fixed_indexer])
    self.plot_data.set_data('alt_fixed', self.alts[fixed_indexer])

    if masked_flag == 1:
      self.plot_data.set_data('cur_lat_spp', [soln.lat])
      self.plot_data.set_data('cur_lng_spp', [soln.lon])
    if masked_flag == 2:
      self.plot_data.set_data('cur_lat_dgps', [soln.lat])
      self.plot_data.set_data('cur_lng_dgps', [soln.lon])
    if masked_flag == 3:
      self.plot_data.set_data('cur_lat_float', [soln.lat])
      self.plot_data.set_data('cur_lng_float', [soln.lon])
    if masked_flag == 4:
      self.plot_data.set_data('cur_lat_fixed', [soln.lat])
      self.plot_data.set_data('cur_lng_fixed', [soln.lon])

    # set-up table variables
    self.pos_table = pos_table
    self.table = self.pos_table + self.vel_table + self.dops_table

    # TODO: figure out how to center the graph now that we have two separate messages
    # when we selectively send only SPP, the centering function won't work anymore

    if self.position_centered:
      d = (self.plot.index_range.high - self.plot.index_range.low) / 2.
      self.plot.index_range.set_bounds(soln.lon - d, soln.lon + d)
      d = (self.plot.value_range.high - self.plot.value_range.low) / 2.
      self.plot.value_range.set_bounds(soln.lat - d, soln.lat + d)
    if self.zoomall:
      plot_square_axes(self.plot, 'lng_spp', 'lat_spp')

  def dops_callback(self, sbp_msg, **metadata):
    dops = MsgDopsDepA(sbp_msg)
    self.dops_table = [
      ('PDOP', '%.1f' % (dops.pdop * 0.01)),
      ('GDOP', '%.1f' % (dops.gdop * 0.01)),
      ('TDOP', '%.1f' % (dops.tdop * 0.01)),
      ('HDOP', '%.1f' % (dops.hdop * 0.01)),
      ('VDOP', '%.1f' % (dops.vdop * 0.01))
    ]
    self.table = self.pos_table + self.vel_table + self.dops_table

  def vel_ned_callback(self, sbp_msg, **metadata):
    vel_ned = MsgVelNEDDepA(sbp_msg)

    tow = vel_ned.tow * 1e-3
    if self.nsec is not None:
      tow += self.nsec * 1e-9

    if self.week is not None:
      t = datetime.datetime(1980, 1, 6) + \
          datetime.timedelta(weeks=self.week) + \
          datetime.timedelta(seconds=tow)
     
      if self.directory_name_v == '':
          filepath_v = time.strftime("velocity_log_%Y%m%d-%H%M%S.csv")
      else:
          filepath_v = os.path.join(self.directory_name_v,time.strftime("velocity_log_%Y%m%d-%H%M%S.csv"))

      if self.logging_v ==  False:
        self.vel_log_file = None

      if self.logging_v:

        if self.vel_log_file is None:
          self.vel_log_file = open(filepath_v, 'w')
          self.vel_log_file.write('time,north(m/s),east(m/s),down(m/s),speed(m/s),num_signals\n')

        

        self.vel_log_file.write('%s,%.6f,%.6f,%.6f,%.6f,%d\n' % (
          str(t),
          vel_ned.n * 1e-3, vel_ned.e * 1e-3, vel_ned.d * 1e-3,
          math.sqrt(vel_ned.n*vel_ned.n + vel_ned.e*vel_ned.e) * 1e-3,
          vel_ned.n_sats)
        )
        self.vel_log_file.flush()

    self.vel_table = [
      ('Vel. N', '% 8.4f' % (vel_ned.n * 1e-3)),
      ('Vel. E', '% 8.4f' % (vel_ned.e * 1e-3)),
      ('Vel. D', '% 8.4f' % (vel_ned.d * 1e-3)),
    ]
    self.table = self.pos_table + self.vel_table + self.dops_table

  def gps_time_callback(self, sbp_msg, **metadata):
    self.week = MsgGPSTimeDepA(sbp_msg).wn
    self.nsec = MsgGPSTimeDepA(sbp_msg).ns

  def __init__(self, link, dirname=''):
    super(SolutionView, self).__init__()

    self.lats = np.empty(self.plot_history_max)
    self.lngs = np.empty(self.plot_history_max)
    self.alts = np.empty(self.plot_history_max)
    self.tows = np.empty(self.plot_history_max)
    self.modes = np.empty(self.plot_history_max)
    self.log_file = None
    self.directory_name_v = dirname
    self.directory_name_p = dirname
    self.vel_log_file = None
    self.last_stime_update = 0
    self.last_soln = None

    self.counter = 0
    self.latitude_list = []
    self.longitude_list = []
    self.altitude_list = []
    self.altitude = 0
    self.longitude = 0
    self.latitude = 0

    self.plot_data = ArrayPlotData(lat_spp=[], lng_spp=[], alt_spp=[],
      cur_lat_spp=[], cur_lng_spp=[], lat_dgps=[], lng_dgps=[], alt_dgps=[],
      cur_lat_dgps=[], cur_lng_dgps=[], lat_float=[], lng_float=[], alt_float=[],
      cur_lat_float=[], cur_lng_float=[], lat_fixed=[], lng_fixed=[], alt_fixed=[],
      cur_lat_fixed=[], cur_lng_fixed=[])
    self.plot = Plot(self.plot_data)

    # 1000 point buffer
    self.plot.plot(('lng_spp', 'lat_spp'), type='line', line_width=0.1, name='', color=color_dict[SPP_MODE])
    self.plot.plot(('lng_spp', 'lat_spp'), type='scatter',  name='', color=color_dict[SPP_MODE],
      marker='dot', line_width=0.0, marker_size=1.0)
    self.plot.plot(('lng_dgps', 'lat_dgps'), type='line',  line_width=0.1, name='', color=color_dict[DGNSS_MODE])
    self.plot.plot(('lng_dgps', 'lat_dgps'), type='scatter', name='', color=color_dict[DGNSS_MODE],
      marker='dot', line_width=0.0, marker_size=1.0)
    self.plot.plot(('lng_float', 'lat_float'), type='line',  line_width=0.1, name='', color=color_dict[FLOAT_MODE])
    self.plot.plot(('lng_float', 'lat_float'), type='scatter', name='', color=color_dict[FLOAT_MODE],
      marker='dot', line_width=0.0, marker_size=1.0)
    self.plot.plot(('lng_fixed', 'lat_fixed'), type='line',  line_width=0.1, name='', color=color_dict[FIXED_MODE])
    self.plot.plot(('lng_fixed', 'lat_fixed'), type='scatter', name='', color=color_dict[FIXED_MODE],
      marker='dot', line_width=0.0, marker_size=1.0)
    # current values
    spp = self.plot.plot(('cur_lng_spp', 'cur_lat_spp'), type='scatter', name=mode_dict[SPP_MODE],
      color=color_dict[SPP_MODE], marker='plus', line_width=1.5, marker_size=5.0)
    dgps = self.plot.plot(('cur_lng_dgps', 'cur_lat_dgps'), type='scatter', name=mode_dict[DGNSS_MODE],
      color=color_dict[DGNSS_MODE], marker='plus', line_width=1.5, marker_size=5.0)
    rtkfloat = self.plot.plot(('cur_lng_float', 'cur_lat_float'), type='scatter', name=mode_dict[FLOAT_MODE],
      color=color_dict[FLOAT_MODE], marker='plus', line_width=1.5, marker_size=5.0)
    rtkfix = self.plot.plot(('cur_lng_fixed', 'cur_lat_fixed'), type='scatter', name=mode_dict[FIXED_MODE],
      color=color_dict[FIXED_MODE], marker='plus', line_width=1.5, marker_size=5.0)
    plot_labels = ['SPP', 'DGPS', "RTK float", "RTK fixed"]
    plots_legend = dict(zip(plot_labels, [spp, dgps, rtkfloat, rtkfix]))
    self.plot.legend.plots = plots_legend
    self.plot.legend.visible = True

    self.plot.index_axis.tick_label_position = 'inside'
    self.plot.index_axis.tick_label_color = 'gray'
    self.plot.index_axis.tick_color = 'gray'
    self.plot.index_axis.title='Longitude (degrees)'
    self.plot.index_axis.title_spacing = 5
    self.plot.value_axis.tick_label_position = 'inside'
    self.plot.value_axis.tick_label_color = 'gray'
    self.plot.value_axis.tick_color = 'gray'
    self.plot.value_axis.title='Latitude (degrees)'
    self.plot.value_axis.title_spacing = 5
    self.plot.padding = (25, 25, 25, 25)

    self.plot.tools.append(PanTool(self.plot))
    zt = ZoomTool(self.plot, zoom_factor=1.1, tool_mode="box", always_on=False)
    self.plot.overlays.append(zt)

    self.link = link
    self.link.add_callback(self._pos_llh_callback, [SBP_MSG_POS_LLH_DEP_A, SBP_MSG_POS_LLH])
    self.link.add_callback(self.vel_ned_callback, [SBP_MSG_VEL_NED_DEP_A, SBP_MSG_VEL_NED])
    self.link.add_callback(self.dops_callback, [SBP_MSG_DOPS_DEP_A, SBP_MSG_DOPS])
    self.link.add_callback(self.gps_time_callback, [SBP_MSG_GPS_TIME_DEP_A, SBP_MSG_GPS_TIME])

    self.week = None
    self.nsec = 0

    self.python_console_cmds = {
      'solution': self,
    }
