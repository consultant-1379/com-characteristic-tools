#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# REQUIREMENTS: gnuplot

import sys
import argparse
import string
import os
import subprocess
import time

from math import log10, floor, pow
from subprocess import Popen, PIPE, STDOUT

from pythonlib.html import HTML

#from Math impoert round

#from framework_Report import red,green,ReportGraph,Report,ReportHtml

# A typical path in Jenkins is:
# /proj/ecomhud/jenkins/jenkins_home/jobs/COM-com-dev-Characteristics/builds/5/archive/test/pt/doc/figures.csv
class Defaults:
	basePath = "/proj/ecomhud/jenkins/jenkins_home/jobs/"
	job = "COM-com-dev-Characteristics"
	resultPath = "archive/test/pt/doc/figures.csv"
	output = "CollectedFigures.csv"
	threshold = "0.0"

# Example fields from figures.csv:
# Thu Feb  4 15:43:53 2016,
# test_NC_replace_rfcmode_setAttributes_100X300moX500bytes,
# fail,
# Memory delta  footprint is not in expected range: 50000.0 kB and 75000.0 kB,
# 50000.0,
# 75000.0,
# 133608.0
# Pre Python 3 simulate enum like this
class Field:
	date = 0
	name = 1
	status = 2
	char = 3
	min = 4
	max = 5
	actualValue = 6
	runNumber = 7

class TestStats:
	testName =""
	valueMin = 0.0
	valueMax = 0.0
	valueAverage = 0.0
	passRate = 0.0

class Sample:
	def __init__(self, value = 0.0, run = 0, date = "", limitMinOld = "", limitMaxOld = ""):
		self.value = value
		self.runNumber = run
		self.date = date
		self.limitMinOld = limitMinOld
		self.limitMaxOld = limitMaxOld

class DataRun:
	def __init__(self, data, run):
		self.data = data
		self.runNumber= run

def red(text): return "\033[1;31m%s\033[0m" % (text)
def green(text): return "\033[2;32m%s\033[0m" % (text)

# round n to d significant digits
# round(1234,    2) => 1200 # convert to int to avoid 1200.0
# round(12.34,   2) => 12
# round(0.01234, 2) => 0.012
def roundSig(n, d):
	if(n == 0): return 0
	n=float(n)
	v = round(n, d -1 -int(floor(log10(abs(n)))))
	if(abs(v) > pow(10, abs(d) - 1)):
		v = int(v)
	return v

g_args = None # share command line args globally

#######################################################################################################################

def main():
	global g_args
	parser = parse()
	g_args = parser.parse_args()

	bigData = readFiles()
	collectedStats = collectStats(bigData)
	filteredStats = filterStats(collectedStats)

	printStats(filteredStats)
	if(g_args.p != None):
		plotStats(filteredStats)
		createHtml(filteredStats)

	return

def readFiles():
	oldData = ""
	bigData = []
	for run in range(int(g_args.first), int(g_args.last) + 1):
		path = os.path.join(g_args.b, g_args.j, "builds", str(run), g_args.r)
		if(os.path.isfile(path)):
			data = open(path, 'r').read()
			if(oldData != [] and oldData == data):
				print path + red(" Duplicate, discarded")
			else:
				print path + green(" OK")
				dataRun = DataRun(data, run)
				bigData.append(dataRun)

			oldData = data
		else:
			print path + red(" MISSING!")

	return bigData


def collectStats(bigData):
	# Rearrange bigData into a 3 dimensional matrix: theMatrix[run][test][field]
	theMatrix = []
	collectedStats = []
	for dataRun in bigData:
		data = dataRun.data
		lines = data.splitlines()
		testResult = []
		for line in lines:
			fields = line.split(",")
			fields.append(dataRun.runNumber)
			testResult.append(fields)

		theMatrix.append(testResult)

	# Now do the job!
	for test in range(1, len(theMatrix[0])):
		valueMin = sys.float_info.max
		valueMax = 0.0
		testTot = 0
		passCount = 0
		samples = []
		for index in range(0, len(theMatrix)):
			value = float(theMatrix[index][test][Field.actualValue])
			sample = Sample(value, theMatrix[index][test][Field.runNumber], theMatrix[index][test][Field.date],
			theMatrix[index][test][Field.min], theMatrix[index][test][Field.max])
			samples.append(sample)
			valueMin = min(valueMin, value)
			valueMax = max(valueMax, value)
			testTot += value
			if(theMatrix[index][test][Field.status] == 'pass'):
				passCount += 1

		valueAverage = testTot / (len(theMatrix) + 1)
		passRate = passCount / (len(theMatrix) + 1)

		testStats = {}
		testStats["testName"] = theMatrix[index][test][Field.name]
		testStats["charName"] = shortenChar(theMatrix[index][test][Field.char])
		testStats["valueMin"] = valueMin
		testStats["valueMax"] = valueMax
		testStats["limitMinOld"] = theMatrix[0][test][Field.min]
		testStats["limitMaxOld"] = theMatrix[0][test][Field.max]
		testStats["valueAverage"] = valueAverage
		if(valueMin != 0.0): testStats["valueSpan"] = (valueMax - valueMin) * 100.0 / valueMin
		else: testStats["valueSpan"] = 100.0
		testStats["limitMinNew"] = valueMin * 0.9
		testStats["limitMaxNew"] = valueMax * 1.1
		testStats["limitMinChange"] = calcChangePercentage(testStats["limitMinOld"], testStats["limitMinNew"])
		testStats["limitMaxChange"] = calcChangePercentage(testStats["limitMaxOld"], testStats["limitMaxNew"])
		testStats["samples"] = samples

		collectedStats.append(testStats)

	return collectedStats

def filterStats(stats):
	if(g_args.w != None and g_args.t != None):
		print "Specifying both -w and -t arguments is ambigous. \nPlease choose one."
		exit(1)

	filteredStats = []
	for stat in stats:
		if(g_args.t != None): # filter with threshold for tightening limits
			t = int(g_args.t)
			if(stat["limitMinChange"] > t or stat["limitMaxChange"] < -t):
				filteredStats.append(stat)

		elif(g_args.w != None): # filter with threshold for widening limits
			w = int(g_args.w)
			if(stat["limitMinChange"] < -w or stat["limitMaxChange"] > w):
				filteredStats.append(stat)

		elif(g_args.c == True): # filter with custom algorithm below
			 # exclude previously filtered by -w 5
			 # include remaining -t 10
			if(not(stat["limitMinChange"] < -5 or stat["limitMaxChange"] > 5)):
				if(stat["limitMinChange"] > 10 or stat["limitMaxChange"] < -10):
					filteredStats.append(stat)

		else: # no filter
			filteredStats.append(stat)

	return filteredStats


def printStats(stats):
	outFile = None
	if g_args.p != None:
		if os.path.isdir(g_args.p) == False:
			os.mkdir(g_args.p)
		outPath = os.path.join(g_args.p, "collected.csv")
		outFile = open(outPath, "w")

	if(g_args.p != None):
		outFile.write("limitMinOld, limitMinNew, limitMinChange, limitMaxOld, limitMaxNew, limitMaxChange, valueSpan, testName, charName \n")
	else:
		print("limitMinOld, limitMinNew, limitMinChange, limitMaxOld, limitMaxNew, limitMaxChange, valueSpan, testName, charName")

	for stat in stats:
		output = ""

		output += '%s'%float(stat["limitMinOld"]) + ",\t"
		output += '%s'%roundSig(stat["limitMinNew"], 2) + ",\t"
		output += '%+d'%roundSig(stat["limitMinChange"], 2) + ",\t"

		output += '%s'%float(stat["limitMaxOld"]) + ",\t"
		output += '%s'%roundSig(stat["limitMaxNew"], 2) + ",\t"
		output += '%+d'%roundSig(stat["limitMaxChange"], 2) + ",\t"

		output += '%s'%roundSig(stat["valueSpan"], 2) + ",\t"

		output += stat["testName"] + ",\t"
		output += stat["charName"]

		if(g_args.p != None):
			outFile.write(output + "\n")
		else:
			print(output)

	return
	# outfile will close as we go out of scope

def plotStats(stats):
	for stat in stats:
		plot(stat)

def createHtml(stats):
	styleHeading1 = "font-family: Arial; font-size:34px; "
	styleHeading2 = "font-family: Arial; font-size:22px; "
	styleTxt = "font-family: Arial; "

	html = HTML()
	html.head.title("Collected Charachteristics")

	body = html.body

	body.p("Collected charachteristics: ", style = styleHeading1)
	body.p("Jenkins job name: " + g_args.j, style = styleHeading2)
	firstDate = stats[0]["samples"][0].date
	lastDate = stats[0]["samples"][len(stats[0]["samples"]) - 1].date
	body.p(firstDate + "  -  " + lastDate, style = styleTxt)
	body.p("Valid measurements: " + str(len(stats)), style = styleTxt)
	body.p("Number of runs: " + str(len(stats[0]["samples"])), style = styleTxt)

	divSummary = body.div(id="summary")
	divSummary.p("Summary", style = styleHeading2)


	index = 0
	for stat in stats:
		divSummary.a(stat["testName"] + " - " + stat["charName"], style = styleTxt, href = "#" + "%d"%index)
		divSummary.br()
		imageFileName = stat["testName"] + "_" + stat["charName"] + ".png"

		body.div(id = "%d"%index)
		body.img(src=imageFileName)
		body.br
		newLimits = "New min: " + str(roundSig(stat["limitMinNew"], 3)) + " New max: " + str(roundSig(stat["limitMaxNew"], 3))
		body.p(newLimits, style = styleTxt)
		body.br
		body.br
		body.br

		index += 1

	divSummary.br
	divSummary.br
	divSummary.br


	htmlPath = os.path.join(g_args.p, "index.html")
	f = open(htmlPath, "w")
	f.write(str(html))
	f.close()

def plot(stats):
	proc = None
	try:
		imageFileName = stats["testName"] + "_" + stats["charName"] + ".png"

		proc = Popen('gnuplot', stdin=PIPE, stdout=PIPE, stderr=PIPE)
		gnuCmd = ''
		gnuCmd += b'set terminal pngcairo dashed  \n'

		gnuCmd += "set style line 1 linecolor rgb 'green'  pointtype 0 linetype 1\n"  # values"
		gnuCmd += "set style line 2 linecolor rgb 'blue'   pointtype 0 linetype 3\n"  # Old limits
		gnuCmd += "set style line 3 linecolor rgb 'purple' pointtype 5 linetype 3\n"  # New limits

#		gnuCmd += "set nokey\n" + \
		gnuCmd += "set output \"" + g_args.p + "/" + imageFileName + "\n"
		gnuCmd += 'set title "' + stats["testName"] + '\\n'  + stats["charName"] +'"\n' # gnuCmd " in title. With ' it fails  \ fails
		gnuCmd += "set key above\n"

		gnuCmd += "plot '-' notitle with lp linestyle 1,"
		gnuCmd += " '-' title 'Old limits' with lp linestyle 2,"
		gnuCmd += " '-' notitle with lp linestyle 2,"
		gnuCmd += " '-' title 'New limits' with lp linestyle 3,"
		gnuCmd += " '-' notitle with lp linestyle 3"
		gnuCmd += "\n"

		# Measured values
		for sample in stats["samples"]:
			gnuCmd += str(sample.runNumber) + " " + str(sample.value) + "\n"
		gnuCmd += "e\n"

		# Min limits
		for sample in stats["samples"]:
			gnuCmd += str(sample.runNumber) + " " + str(sample.limitMinOld) + "\n"
		gnuCmd += "e\n"

		# Max limits
		for sample in stats["samples"]:
			gnuCmd += str(sample.runNumber) + " " + str(sample.limitMaxOld) + "\n"
		gnuCmd += "e\n"

		firstRun = str(stats["samples"][0].runNumber)
		lastRun = str(stats["samples"][len(stats["samples"])-1].runNumber)

		limitMinNew = str(stats["limitMinNew"])
		gnuCmd += firstRun + " " + limitMinNew + "\n"
		gnuCmd += lastRun + " " + limitMinNew + "\n"
		gnuCmd += "e\n"

		limitMaxNew = str(stats["limitMaxNew"])
		gnuCmd += firstRun + " " + limitMaxNew + "\n"
		gnuCmd += lastRun + " " + limitMaxNew + "\n"
		gnuCmd += "e\n"

		proc.stdin.write(gnuCmd)

	except OSError:
		print("Error, failed to find gnuplot.")
		sys.exit()

def calcChangePercentage(old, new):
	old = float(old)
	new = float(new)
	if(old == 0 and new == 0): return 0
	if(old == 0 and new != 0): return 100

	change = int((new - old) * 100 / old)
	return change

def shortenChar(char):
	pos = char.find(" is ")
	if(pos != -1):
		return char[:pos]
	else:
		return char

def parse():
  parser=argparse.ArgumentParser(description='Collect data from a series of charachteristics tests, typically from Jenkins.',
  formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument('first',  type=int, help='# of first job')
  parser.add_argument('last',  type=int, help='# of last job')
  parser.add_argument('-b', metavar='base', default=Defaults.basePath,       help='Jenkins base path \t[' + Defaults.basePath + ']')
  parser.add_argument('-j', metavar='job', default=Defaults.job,             help='Jenkins job       \t[' + Defaults.job + ']')
  parser.add_argument('-r', metavar='result', default=Defaults.resultPath,   help='Result path       \t[' + Defaults.resultPath + ']')
  parser.add_argument('-p', metavar='threshold', help='Plot graphs & HTML directory')
  parser.add_argument('-w', metavar='threshold', help='Filter for "wider threshold"')
  parser.add_argument('-t', metavar='threshold', help='Filter for "tighter threshold"')
  parser.add_argument('-c', action='store_const', const = True, help='Filter for custom threshold, code. See "custom" in code.')


  epilog = 'Example: \n'
  epilog += '  collectResults 5 15                           Collect results from the default job, runs #5 - #15  \n'
  epilog += '  collectResults 5 15 -j COM51-Characteristics  Collect results from COM51-Characteristics, runs #5 - #15 \n'
  epilog += '  collectResults 5 15 -w 5                      Filter: min change < -5% or max change > 5% \n'
  epilog += '  collectResults 5 15 -n 10                     Filter: min change > 10% or max change < -10% \n'
  parser.epilog = epilog
  return parser

main()
