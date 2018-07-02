#!/usr/bin/env python
# -*- python -*-
#
# Git Version: @git@

#-----------------------------------------------------------------------
# XALT: A tool to track the programs on a cluster.
# Copyright (C) 2013-2014 Robert McLay and Mark Fahey
# 
# This library is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation; either version 2.1 of 
# the License, or (at your option) any later version. 
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser  General Public License for more details. 
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free
# Software Foundation, Inc., 59 Temple Place, Suite 330,
# Boston, MA 02111-1307 USA
#-----------------------------------------------------------------------

#  xalt_syslog takes the output found in xalt syslog file
#  provided with the --syslog_file=ans argument and puts it 
#  into the database
#
#  One has to set up syslog to capture the XALT output
#  Example is provided in the xalt_syslog.conf file that can 
#  be put in the /etc/rsyslog.d directory.
#
#

from __future__ import print_function
from __future__ import division
import os, sys, re, MySQLdb, json, time, argparse, base64, zlib, shlex, random

dirNm, execName = os.path.split(os.path.realpath(sys.argv[0]))
sys.path.insert(1,os.path.realpath(os.path.join(dirNm, "../libexec")))
sys.path.insert(1,os.path.realpath(os.path.join(dirNm, "../site")))

from XALTdb        import XALTdb
from xalt_util     import *
from xalt_global   import *
from progressBar   import ProgressBar
from Rmap_XALT     import Rmap

import inspect

def __LINE__():
    try:
        raise Exception
    except:
        return sys.exc_info()[2].tb_frame.f_back.f_lineno

def __FILE__():
    return inspect.currentframe().f_code.co_filename

#print ("file: '%s', line: %d" % (__FILE__(), __LINE__()), file=sys.stderr)

ConfigBaseNm = "xalt_db"
ConfigFn     = ConfigBaseNm + ".conf"
logger       = config_logger()

class CmdLineOptions(object):
  """ Command line Options class """

  def __init__(self):
    """ Empty Ctor """
    pass
  
  def execute(self):
    """ Specify command line arguments and parse the command line"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--syslog",      dest='syslog',   action="store",      help="Location and name of syslog file")
    parser.add_argument("--syshost",     dest='syshost',  action="store",      default=".*",
                                                                               help="Store just records that come from syshost")
    parser.add_argument("--leftover_fn", dest='leftover', action="store",      default='leftover.log',
                                                                               help="Name of the leftover file")
    parser.add_argument("--timer",       dest='timer',    action="store_true", help="Time runtime")
    parser.add_argument("--reverseMapD", dest='rmapD',    action="store",      help="Path to the directory containing the json reverseMap")
    parser.add_argument("--confFn",      dest='confFn',   action="store",      default="xalt_db.conf", help="Name of the database")
    args = parser.parse_args()
    return args


class Record(object):
  """
  This class holds the pieces of a single record as it comes in from
  syslog
  """
  def __init__(self, t, old=False):
    nblks          = int(t['nb'])
    self.__nblks   = nblks
    self.__kind    = t['kind']
    self.__syshost = t['syshost']
    self.__old     = old
    self.__blkCnt  = 0

    blkA = []
    for i in range(nblks):
      blkA.append(False)

    self.__blkA    = blkA
    self.addBlk(t)

  def addBlk(self,t):
    idx               = int(t['idx'])
    if (0 <= idx and idx < self.__nblks and not self.__blkA[idx] ):
      self.__blkA[idx]  = t['value']
      self.__blkCnt    += 1
    elif (idx < 0 or idx >= self.__nblks):
      raise ValueError("Bad block index")
        
    
  def completed(self):
    return (self.__blkCnt >= self.__nblks)

  def value(self):
    return "".join(self.__blkA)

  def prt(self, key):
    if (self.__old):
      return None

    sA    = []
    nblks = self.__nblks
    blkA  = self.__blkA

    sPA   = []
    
    sPA.append("XALT_LOGGING_")
    sPA.append(self.__syshost)
    sPA.append(" V:2 kind:")
    sPA.append(self.__kind)
    sPA.append(" syshost:")
    sPA.append(self.__syshost)
    sPA.append(" key:")
    sPA.append(key)
    sPA.append(" nb:")
    sPA.append(str(nblks))
    ss = "".join(sPA)

    for idx in range(nblks):
      value = blkA[idx]
      if (value):
        sA.append(ss)
        sA.append(" idx:")
        sA.append(str(idx))
        sA.append(" value:")
        sA.append(value)
    return "".join(sA)

class ParseSyslog(object):
  """
  """
  def __init__(self, leftoverFn):
    self.__recordT    = {}
    self.__leftoverFn = leftoverFn

  def writeRecordT(self):
    leftoverFn = self.__leftoverFn
    if (os.path.isfile(leftoverFn)):
      os.rename(leftoverFn, leftoverFn + ".old")

    recordT = self.__recordT
    if (recordT):
      f = open(leftoverFn, "w")
      for key in self.__recordT:
        r = recordT[key]
        s = r.prt(key)
        if (s):
          f.write(s)
          
      f.close()
    

  def parse(self, s, clusterName, old):
    if ("XALT_LOGGING_" in s):
      if (" V:2 " in s):
        return self.__parseSyslogV2(s, clusterName, old)
      else:
        return self.__parseSyslogV1(s, clusterName)
    return false, {}

  def __parseSyslogV1(self, s, clusterName):
    t = { 'kind' : None, 'value' : None, 'syshost' : None, 'version' : 1}

    idx          = s.find("XALT_LOGGING_")
    idx         += 13
    p            = s[idx:].find(" ")
    t['syshost'] = s[idx:idx+p]
    

    idx = s.find("link:")
    if (idx == -1):
      idx = s.find("run:")
    if (idx == -1):
      return t, False

    array        = s[idx:].split(":")
    t['kind']    = array[0].strip()
    t['value']   = base64.b64decode(array[1])

    if (clusterName != ".*" and clusterName != t['syshost']):
      return t, False

    return t, True
    
  def __parseSyslogV2(self, s, clusterName, old):
    t = { 'kind' : None, 'syshost' : None, 'value' : None, 'version' : 2}

    idx = s.find(" V:2 ")
    if (idx  == -1):
      return t, False

    # Strip off "XALT_LOGGING V:2" from string.
    s                      = s[idx+5:]

    # Setup parser
    lexer                  = shlex.shlex(s)
    lexer.whitespace_split = True
    lexer.whitespace       = ' :'

    # Pick off two values at a time.
    try: 
      while True:
        key    = next(lexer)
        value  = next(lexer)
        t[key] = value
    except StopIteration as e:
      pass
  
    if (clusterName != ".*" and clusterName != t['syshost']):
      return t, False


    recordT = self.__recordT

    # get the key from the input, then place an entry in the *recordT* table.
    # or just add the block to the current record.
    key  = t['key']
    r    = recordT.get(key, None)
    if (r):
      r.addBlk(t)
    else:
      r            = Record(t, old)
      recordT[key] = r

    # If the block is completed then grap the value, remove the entry from *recordT*
    # and return a completed table.
    if (r.completed()):
      
      rv   = r.value()
      b64v = base64.b64decode(rv)
      vv   = zlib.decompress(b64v)

      t['value'] = vv
      recordT.pop(key)
      return t, True

    # Entry is not complete.
    return t, False
    
class Filter(object):

  def __init__(self, maxJobsSaved):
    self.__jobT = {}
    self.__num  = maxJobsSaved

  def register(self, runT):

    # ignore a start record or mpi executable
    if (runT['userDT']['end_time'] <= 0.0 or runT['userDT']['num_cores'] > 1):
      return

    jobT                 = self.__jobT
    userT                = runT['userT']
    userDT               = runT['userDT']
    job_id               = userT.get('job_id',"0")
    entry                = jobT.get(job_id, { 'Nexecs' : 0, 'total_time' : 0.0, 'Nsaved' : 0 })
    entry['Nexecs']     += 1
    entry['total_time'] += userDT['run_time']
    jobT[job_id]         = entry

  def report_stats(self):
    jobT = self.__jobT

    icnt          = 0
    N             = 4
    numScalarJobs = len(jobT)
    maxExecCnt    = 0

    for job_id in sorted(jobT, key=lambda x : jobT[x]['Nexecs'], reverse=True):
      icnt += 1
      if (icnt <= N):
        print ("job_id:",job_id,"Num of exec:", jobT[job_id]['Nexecs'], "Total time:",jobT[job_id]['total_time'])
      maxExecCnt += jobT[job_id]['Nexecs']
      
    print("Number of job_id's:",numScalarJobs,"Total number of scalar executables: ", maxExecCnt)


  def apply(self, runT):

    if (runT['userDT']['end_time'] <= 0.0 or runT['userDT']['num_cores'] > 1):
      return True

    job_id       = runT['userT'].get('job_id',"0")
    jobT         = self.__jobT
    maxJobsSaved = self.__num
    entry        = jobT[job_id]

    Nexecs       = entry['Nexecs']
    if ( Nexecs <= maxJobsSaved):
      return True

    if (entry['Nsaved'] >= maxJobsSaved):
      return False

    if (entry['Nsaved'] == 0 or random.random() < maxJobsSaved/Nexecs):
      runT['userDT']['sum_runs']  = Nexecs
      runT['userDT']['sum_times'] = entry['total_time']
      jobT[job_id]['Nsaved'] += 1
      return True

    return False

def main():
  """
  read from syslog file into XALT db.
  """

  sA = []
  sA.append("CommandLine:")
  for v in sys.argv:
    sA.append('"'+v+'"')
  XALT_Stack.push(" ".join(sA))

  args       = CmdLineOptions().execute()
  xalt       = XALTdb(args.confFn)
  syslogFile = args.syslog

  icnt   = 0
  t1     = time.time()

  try:
    rmapT  = Rmap(args.rmapD).reverseMapT()
  except Exception as e:
    print(e, file=sys.stderr)
    print("Failed to read reverseMap file -> exiting")
    sys.exit(1)

  lnkCnt = 0
  pkgCnt = 0
  runCnt = 0
  badCnt = 0
  count  = 0

  recordT = {}

  fnA    = [ args.leftover, syslogFile ]

  parseSyslog = ParseSyslog(args.leftover)


  #-----------------------------
  # Figure out size in bytes.

  fnSz = 0
  for fn in fnA:
    if (not os.path.isfile(fn)):
      continue
    fnSz  += os.path.getsize(fn)
    

  #----------------------------------------------------------
  # Count the number and sum the run_time for all scalar jobs

  filter = Filter(100)
  if fnSz == 0:
    fnSz = 1
  pbar   = ProgressBar(maxVal=fnSz,fd=sys.stdout)
  for fn in fnA:
    if (not os.path.isfile(fn)):
      continue

    old = (fn == args.leftover)
    
    lineNo = 0    
    f=open(fn, 'r')
    for line in f:
      lineNo += 1    
      count  += len(line)
      pbar.update(count)
      if (not ("XALT_LOGGING" in line)):
        continue
      try:
        t, done = parseSyslog.parse(line, args.syshost, old)
      except Exception as e:
        #print(e, file=sys.stderr)
        #print("lineNo:",lineNo,"file:",fn,"line:",line, file=sys.stderr)
        #print("Now continuing processing!", file=sys.stderr)
        continue

      
      if (not done or t['kind'] != "run"):
        continue


      ##################################
      # If the json conversion fails,
      # then ignore record and keep going
      value = False

      try:
        value = json.loads(t['value'])
        filter.register(value)
      except Exception as e:
        #print("fn:",fn,"line:",lineNo,"value:",t['value'],file=sys.stderr)
        continue

    f.close()
  pbar.fini()

  filter.report_stats()
  
  badsyslog   = 0
  count       = 0
  parseSyslog = ParseSyslog(args.leftover)
  pbar        = ProgressBar(maxVal=fnSz,fd=sys.stdout)
  for fn in fnA:
    if (not os.path.isfile(fn)):
      continue

    old = (fn == args.leftover)

    f=open(fn, 'r')
    for line in f:
      count += len(line)
      pbar.update(count)
      if (not ("XALT_LOGGING" in line)):
        continue
      try:
        t, done = parseSyslog.parse(line, args.syshost, old)
      except Exception as e:
        badsyslog += 1
        continue
      
      if (not done):
        continue

      ##################################
      # If the json conversion fails,
      # then ignore record and keep going
      try:
        value = json.loads(t['value'])
      except Exception as e:
        continue

      try:
        XALT_Stack.push("XALT_LOGGING: " + t['kind'] + " " + t['syshost'])

        if ( t['kind'] == "link" ):
          XALT_Stack.push("link_to_db()")
          xalt.link_to_db(rmapT, value)
          XALT_Stack.pop()
          lnkCnt += 1
        elif ( t['kind'] == "run" ):
          if (filter.apply(value)):
            XALT_Stack.push("run_to_db()")
            xalt.run_to_db(rmapT, value)
            XALT_Stack.pop()
            runCnt += 1
        elif ( t['kind'] == "pkg" ):
          XALT_Stack.push("pkg_to_db()")
          xalt.pkg_to_db(t['syshost'], value)
          XALT_Stack.pop()
          pkgCnt += 1
        else:
          print("Error in xalt_syslog_to_db", file=sys.stderr)
        XALT_Stack.pop()
      except Exception as e:
        print(e, file=sys.stderr)
        badCnt += 1


    f.close()

  pbar.fini()

  t2 = time.time()
  rt = t2 - t1
  if (args.timer):
    print("Time: ", time.strftime("%T", time.gmtime(rt)))
  print("total processed : ", count, ", num links: ", lnkCnt, ", num runs: ", runCnt,
          ", pkgCnt: ", pkgCnt, ", badCnt: ", badCnt, ", badsyslog: ",badsyslog)
        
  
  # if there is anything left in recordT file write it out to the leftover file.
  parseSyslog.writeRecordT()


if ( __name__ == '__main__'): main()
