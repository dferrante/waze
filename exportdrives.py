import requests, os, commands, datetime, sys, simplekml, numpy, re, math, collections
from gnosis.xml.objectify import make_instance

#configurables
outfile = 'drives.kml' # where final kml is written
kmlfolderrules = [
    #('morning-old', lambda x: x['startdate'] < datetime.datetime(2013, 8, 5) and x['startdate'].weekday() < 5 and x['startdate'].hour >= 7 and x['startdate'].hour <= 10 and x['distance'] >= 35 and x['distance'] <= 50),
    ('morning', lambda x: x['startdate'] >= datetime.datetime(2013, 8, 5) and x['startdate'].weekday() < 5 and x['startdate'].hour >= 7 and x['startdate'].hour <= 10 and x['distance'] >= 45 and x['distance'] <= 52),
    #('evening-old', lambda x: x['startdate'] < datetime.datetime(2013, 8, 5) and x['startdate'].weekday() < 5 and x['startdate'].hour >= 16 and x['startdate'].hour <= 19 and x['distance'] >= 35 and x['distance'] <= 50),
    ('evening', lambda x: x['startdate'] >= datetime.datetime(2013, 8, 5) and x['startdate'].weekday() < 5 and x['startdate'].hour >= 16 and x['startdate'].hour <= 19 and x['distance'] >= 46 and x['distance'] <= 52),
    ('long trips', lambda x: x['distance'] >= 150),
    ('other', lambda x: True),
] # use these to sort your drives so you can suss out your commute.  should always end with a catch-all that evals to True.
commutes = ['morning', 'evening'] # which of the above are regular routes
removegmlfiles = True # delete .gml files after downloading
timeslices = 10 # minutes per bucket to break up commutes by time, must be factor of 60
top_and_bottom_ranking_limit = 10
recent_drives_count = 40

#waze API urls
session_url = "https://www.waze.com/login/create"
sessiondata_url = "https://www.waze.com/Descartes-live/app/Archive/Session"
sessionlist_url = "https://www.waze.com/Descartes-live/app/Archive/List"

def export(username, password):
    # login
    req = requests.post(session_url, data={'user_id': username, 'password': password})
    try:
        authdict = dict(req.cookies)
    except:
        print 'login failed, check credentials'
        sys.exit(255)

    # get sessions
    print 'getting sessions'
    sessionlist = []
    offset = 0
    sessions = requests.get(sessionlist_url, params={'count': 50, 'offset': offset}, cookies=authdict).json()['archives']['objects']
    while sessions:
        sessionlist += [x for x in sessions]
        offset += 50
        sessions = requests.get(sessionlist_url, params={'count': 50, 'offset': offset}, cookies=authdict).json()['archives']['objects']
    print 'got %s sessions' % len(sessionlist)
    print 'done'

    print 'getting gml files'
    c = 1
    for session in sessionlist:
        try:
            starttime = datetime.datetime.fromtimestamp(session['startTime']/1000)
            endtime = datetime.datetime.fromtimestamp(session['endTime']/1000)
            length = round(session['totalRoadMeters']*.000621371, 1)
            filename = '%s-%s-%smi' % (starttime.strftime('%y-%m-%d-%H:%M'), endtime.strftime('%y-%m-%d-%H:%M'), length)
        except:
            continue
        gmlfile = 'data/%s.gml' % filename
        gfsfile = 'data/%s.gfs' % filename
        kmlfile = 'data/%s.kml' % filename
        if not os.path.exists(gmlfile) and not os.path.exists(kmlfile):
            data = requests.get(sessiondata_url, params={'id': session['id']}, cookies=authdict)
            try:
                gml = data.json()['archiveSessions']['objects'][0]['data']
            except Exception, e:
                if 'code' in data.json() and data.json()['code'] == 101:
                    print 'the rest are invalid, stopping scan'
                    return
                else:
                    print 'error:', data.url, data.content
                continue
            f = open(gmlfile, 'w')
            f.write(gml)
            f.close()
            commands.getstatusoutput('ogr2ogr -f "KML" %s %s' % (kmlfile, gmlfile))
            if removegmlfiles:
                os.remove(gmlfile)
            os.remove(gfsfile)
            print 'wrote %s (%s/%s)' % (gmlfile, c, len(sessionlist))
            c += 1


def colorspeed(speed):
    if speed == -1: # special case
        return '66000000'

    alpha = 200
    speed = speed-10
    maxspeed = 90.0
    midpoint = maxspeed/2.0
    limiter = lambda x: 255 if x > 255 else 0 if x < 0 else int(x)

    argb = (
        alpha,
        0 if speed <= midpoint else 255*((speed-midpoint)/midpoint),
        255*(speed/midpoint) if speed <= midpoint else 255*(1-((speed-midpoint)/midpoint)),
        255*(1-(speed/midpoint)) if speed <= midpoint else 0,
    )
    argb = tuple(map(limiter, argb))
    color = '%02x%02x%02x%02x' % argb
    return color

def datadict(data):
    d = {}
    for item in data:
        d[item.name] = item.PCDATA
    return d

def averagetime(dates):
    avgseconds = numpy.mean([date.hour * 60 * 60 + date.minute * 60 + date.second for date in dates])
    return '%s:%s' % (int(avgseconds / 3600), int(avgseconds%60))

def parsekmlname(name):
    try:
        sd = map(int,name[:-4].split('-')[:3])
        st = map(int,name[:-4].split('-')[3].split(':'))
        ed = map(int,name[:-4].split('-')[4:7])
        et = map(int,name[:-4].split('-')[7].split(':'))
        distance = float(name[:-4].split('-')[-1][:-2])
        startdate = datetime.datetime(2000+sd[0], sd[1], sd[2], st[0], st[1])
        enddate = datetime.datetime(2000+ed[0], ed[1], ed[2], et[0], et[1])
        triptime = int((enddate-startdate).seconds/60.0)
        avgspeed = round(distance/((enddate-startdate).seconds/3600.0),1)

        fmtname = '%s-%s (%smi/%smin/%smph)' % (startdate.strftime('%m/%d %I:%M%p'), enddate.strftime('%I:%M%p'), distance, triptime, avgspeed)
        self = {'filename': name, 'distance': distance, 'startdate': startdate,
                'enddate': enddate, 'avgspeed': avgspeed, 'fmtname': fmtname}

        for folder, rule in kmlfolderrules:
            if rule(self):
                self['type'] = folder
                return self
    except:
        return False

def commutesplitbucket(folder, buckets, drivebuckets, linedata, linelimit=0):
    averages = {}
    for drivetype in commutes:
        averages[drivetype] = folder.newfolder(name=drivetype, visibility=0)
        averages[drivetype+'-avg'] = folder.newfolder(name='%s vs. avg' % drivetype, visibility=0)
        for bucket in sorted(list(set([x[3] for x in buckets.keys() if x[2] == drivetype]))):
            drivecount = len(drivebuckets[(bucket, drivetype)])
            avglength = round(numpy.mean([x[0] for x in drivebuckets[(bucket, drivetype)]]), 1)
            avgspeed = round(numpy.mean([x[1] for x in drivebuckets[(bucket, drivetype)]]), 1)
            foldername = '%s (%s drives/%smi/%smph)' % (bucket, drivecount, avglength, avgspeed)
            averages[drivetype+bucket] = averages[drivetype].newfolder(name=foldername, visibility=0)
            averages[drivetype+bucket+'-speed'] = averages[drivetype+bucket].newfolder(name='speed points', visibility=0)
            averages[drivetype+bucket+'-avg'] = averages[drivetype+'-avg'].newfolder(name=foldername, visibility=0)
            averages[drivetype+bucket+'-avgspeed'] = averages[drivetype+bucket+'-avg'].newfolder(name='speed points', visibility=0)

    for k,v in buckets.iteritems():
        prevlinename, name, drivetype, bucket = k
        speeds, coords, lengths, dates = zip(*v)

        #exclude non-commutes
        if drivetype not in commutes:
            continue
        if len(v) < linelimit:
            continue

        avgspeed = numpy.mean(speeds)
        length = numpy.mean(lengths)
        avgdate = averagetime(dates)
        coords = max(coords, key=lambda x: len(x)) #pick one with most coords
        display_name = '%s %s (%s)' % (avgdate, name, len(v))
        makespeedline(averages[drivetype+bucket], averages[drivetype+bucket+'-speed'], display_name, coords, avgspeed, length)

        avgdrivespeed = numpy.mean([speed for speed, coords, length, date in linedata[(prevlinename, name, drivetype)]])
        if avgdrivespeed > 0:
            speeddiff = int(avgspeed-avgdrivespeed)
            if speeddiff == 0:
                avgavgspeed, speedlabel = -1, ""
            elif speeddiff > 0:
                avgavgspeed, speedlabel = avgspeed/float(avgdrivespeed)*55+15, speeddiff
            else:
                avgavgspeed, speedlabel = avgspeed/float(avgdrivespeed)*55-15, speeddiff

            makespeedline(averages[drivetype+bucket+'-avg'], averages[drivetype+bucket+'-avgspeed'], display_name, coords, avgavgspeed, length, speeddiff)

def drivesplitbucket(drivefolder, drivetypes, drivedata, linedata, sortkey, topcount=20, bottomcount=0):
    for drivetype in drivetypes:
        subfolder = drivefolder.newfolder(name=drivetype, visibility=0)
        avgsubfolder = drivefolder.newfolder(name="%s vs. avg" % drivetype, visibility=0)

        drivelist = sorted(drivedata[drivetype], key=lambda x: x[sortkey], reverse=True)[:topcount]
        if bottomcount:
            drivelist += sorted(drivedata[drivetype], key=lambda x: x[sortkey], reverse=True)[-bottomcount:]

        for drive in drivelist:
            folder = subfolder.newfolder(name=drive['fmtname'], visibility=0)
            spfolder = folder.newfolder(name='speed labels')
            avgfolder = avgsubfolder.newfolder(name=drive['fmtname'], visibility=0)
            avgspfolder = avgfolder.newfolder(name='speed labels')

            prevlinename = 'start'
            for name, coords, speed, length, date in sorted(drive['lines'], key=lambda x:x[4]):
                display_name = '%s %s' % (date.strftime('%H:%M'), name)
                makespeedline(folder, spfolder, display_name, coords, speed, length)
                avgdrivespeed = numpy.mean([s for s, c, l, d in linedata[(prevlinename, name, drivetype)]])
                if avgdrivespeed > 0:
                    speeddiff = int(speed-avgdrivespeed)
                    if speeddiff == 0:
                        avgavgspeed, speedlabel = -1, ""
                    elif speeddiff > 0:
                        avgavgspeed, speedlabel = speed/float(avgdrivespeed)*55+15, speeddiff
                    else:
                        avgavgspeed, speedlabel = speed/float(avgdrivespeed)*55-15, speeddiff

                    makespeedline(avgfolder, avgspfolder, display_name, coords, avgavgspeed, length, speedlabel)
                prevlinename = name

def makespeedline(folder, spfolder, name, coords, speed, length, speedlabel=None):
    line = folder.newlinestring(coords=coords, name='%s - %smi - %smph' % (name, length, int(speed)))
    line.style.linestyle.width = 6
    line.style.linestyle.color = colorspeed(speed)
    line.tessellate = 1

    if not folder.visibility:
        line.visibility = 0

    avgx = numpy.mean(map(float, [x[0] for x in coords]))
    avgy = numpy.mean(map(float, [x[1] for x in coords]))

    speedlabel = '%s' % (speedlabel if speedlabel is not None else int(speed))
    if speedlabel:
        point = spfolder.newpoint(name=speedlabel, coords=[(avgx, avgy),])
        point.iconstyle.icon.href = ''
        point.style.labelstyle.color = colorspeed(speed)
        point.style.labelstyle.scale = 0.85

        if not folder.visibility:
            point.visibility = 0

def buildreports():
    print 'starting report'
    kmlfiles = sorted([parsekmlname(x) for x in os.listdir('./data') if '.kml' in x if parsekmlname(x)], key=lambda x: x['startdate'])

    #parse kml files with gnosis
    print 'parsing kml files'
    drivedata = collections.defaultdict(list)
    linedata = collections.defaultdict(list)
    linebuckets = collections.defaultdict(list)
    drivelinebuckets = collections.defaultdict(list)
    timebuckets = collections.defaultdict(list)
    drivetimebuckets = collections.defaultdict(list)
    weekbuckets = collections.defaultdict(list)
    driveweekbuckets = collections.defaultdict(list)
    monthbuckets = collections.defaultdict(list)
    drivemonthbuckets = collections.defaultdict(list)
    weekdaybuckets = collections.defaultdict(list)
    driveweekdaybuckets = collections.defaultdict(list)
    distancebuckets = collections.defaultdict(list)
    drivedistancebuckets = collections.defaultdict(list)
    for kmlfile in kmlfiles:
        print '.',
        if kmlfile['distance'] < 1:
            continue

        kmldata = make_instance(open('./data/'+kmlfile['filename']).read())
        try:
            lines = kmldata.Document.Folder.Placemark
            if not lines:
                continue
        except:
            continue

        prevlinename = 'start'
        weekbucketname = kmlfile['startdate'].strftime('%Y-%W')
        weekdaybucketname = kmlfile['startdate'].strftime('(%w) %A')
        monthbucketname = kmlfile['startdate'].strftime('%Y-%m')
        timebucketname = '%s:%02d%s' % (int(kmlfile['startdate'].strftime('%I')),
                                        math.floor(kmlfile['startdate'].minute/60.0*(60/timeslices))*timeslices,
                                        kmlfile['startdate'].strftime('%p').lower())
        distancebucketname = str(kmlfile['distance'])
        kmlfile['lines'] = []
        for l in lines:
            try:
                data = datadict(l.ExtendedData.SchemaData.SimpleData)
            except:
                continue

            status = data['status']
            if status != 'OK':
                continue

            name = data['Name'].strip(',') if 'Name' in data and data['Name'] else ''
            name = re.sub(',', ', ', name)
            name = re.sub('  ', ' ', name)
            name = name.strip()
            length = round(int(data['length'])*0.000621371,1) #convert meters to miles
            speed = int(int(data['speed'])*0.621371) #convert kmh to mph
            coords = [tuple(x.split(',')) for x in l.LineString.coordinates.PCDATA.split()]
            linetime = map(int, [x for x in data['start_time'].split(':')])
            if linetime[0] < 0:
                linetime[0] += 24
            date = datetime.datetime(kmlfile['startdate'].year, kmlfile['startdate'].month, kmlfile['startdate'].day,
                                     linetime[0], linetime[1], linetime[2])
            date += datetime.timedelta(hours=-5)
            if date < kmlfile['startdate']:
                date += datetime.timedelta(days=1)
            fullname = '%s - %s' % (prevlinename, name)

            if speed > 120:
                continue

            kmlfile['lines'].append((name, coords, speed, length, date))
            linedata[(prevlinename, name, kmlfile['type'])].append((speed, coords, length, date))
            linedata[(prevlinename, name, 'all')].append((speed, coords, length, date))
            timebuckets[(prevlinename, name, kmlfile['type'], timebucketname)].append((speed, coords, length, date))
            weekbuckets[(prevlinename, name, kmlfile['type'], weekbucketname)].append((speed, coords, length, date))
            linebuckets[(prevlinename, name, kmlfile['type'], fullname)].append((speed, coords, length, date))
            drivelinebuckets[(fullname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
            weekdaybuckets[(prevlinename, name, kmlfile['type'], weekdaybucketname)].append((speed, coords, length, date))
            monthbuckets[(prevlinename, name, kmlfile['type'], monthbucketname)].append((speed, coords, length, date))
            distancebuckets[(prevlinename, name, kmlfile['type'], distancebucketname)].append((speed, coords, length, date))
            prevlinename = name
        drivedata[kmlfile['type']].append(kmlfile)
        drivedata['all'].append(kmlfile)
        drivetimebuckets[(timebucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
        driveweekbuckets[(weekbucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
        driveweekdaybuckets[(weekdaybucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
        drivemonthbuckets[(monthbucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
        drivedistancebuckets[(distancebucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))


    print '\nbuilding kmls'
    kmloutput = collections.defaultdict(list)
    for kof in ['drives', 'drives by length', 'commutes by speed', 'commutes by depart time', 'commutes by week',
                'commutes by month', 'commutes by weekday', 'averages', 'top speeds', 'commutes by distance', 'segments']:
        kmloutput[kof] = simplekml.Kml(visibility=0)

    sortedfoldernames = [folder for folder, rule in kmlfolderrules]

    print 'drives'
    drivesplitbucket(kmloutput['drives'], sortedfoldernames, drivedata, linedata, 'startdate', recent_drives_count)

    print 'segments'
    commutesplitbucket(kmloutput['segments'], linebuckets, drivelinebuckets, linedata)

    print 'drives by length'
    drivesplitbucket(kmloutput['drives by length'], sortedfoldernames + ['all'], drivedata, linedata, 'distance', 10, 10)

    print 'commutes by speed'
    drivesplitbucket(kmloutput['commutes by speed'], commutes, drivedata, linedata, 'avgspeed', 10, 10)

    print 'commutes by start time interval'
    commutesplitbucket(kmloutput['commutes by depart time'], timebuckets, drivetimebuckets, linedata)

    print 'commutes by week'
    commutesplitbucket(kmloutput['commutes by week'], weekbuckets, driveweekbuckets, linedata)

    print 'commutes by month'
    commutesplitbucket(kmloutput['commutes by month'], monthbuckets, drivemonthbuckets, linedata)

    print 'commutes by day of week'
    commutesplitbucket(kmloutput['commutes by weekday'], weekdaybuckets, driveweekdaybuckets, linedata)

    print 'commutes by distance'
    commutesplitbucket(kmloutput['commutes by distance'], distancebuckets, drivedistancebuckets, linedata)

    print 'averages'
    averages = {}
    for drivetype in sortedfoldernames + ['all']:
        averages[drivetype] = kmloutput['averages'].newfolder(name=drivetype, visibility=0)
        averages[drivetype+'-speed'] = averages[drivetype].newfolder(name='speed labels', visibility=0)

    for k,v in sorted(linedata.iteritems(), key=lambda x:x[0][1]):
        prevlinename, name, drivetype = k
        if not v:
            continue
        if len(v) <= 5 and drivetype in commutes:
            continue
        speeds, coords, lengths, dates = zip(*v)
        avgspeed = numpy.mean(speeds)
        length = numpy.mean(lengths)
        coords = max(coords, key=lambda x: len(x)) #pick one with most coords
        avgdate = averagetime(dates)
        display_name = '%s %s' % (avgdate, name)
        makespeedline(averages[drivetype], averages[drivetype+'-speed'], display_name, coords, avgspeed, length)

    print 'top speeds'
    topspeeds = {}
    for drivetype in sortedfoldernames + ['all']:
        topspeeds[drivetype] = kmloutput['top speeds'].newfolder(name=drivetype, visibility=0)
        topspeeds[drivetype+'-speed'] = topspeeds[drivetype].newfolder(name='speed labels', visibility=0)

        for k,v in sorted(filter(lambda x: x[0][2] == drivetype, linedata.iteritems()), key=lambda x: x[0][1], reverse=True):
            prevlinename, name, drivetype = k
            if not v:
                continue
            speeds, coords, lengths, dates = zip(*v)
            topspeed = max(speeds)
            length = numpy.mean(lengths)
            coords = max(coords, key=lambda x: len(x)) #pick one with most coords
            avgdate = averagetime(dates)
            display_name = '%s %s' % (avgdate, name)
            makespeedline(topspeeds[drivetype], topspeeds[drivetype+'-speed'], display_name, coords, topspeed, length)

    for name, kml in kmloutput.items():
        outfile = '%s.kml' % name
        print 'writing', outfile
        kml.save(outfile)

if __name__ == '__main__':
    username = raw_input('username: ')
    password = raw_input('password: ')
    export(username, password)
    buildreports()
