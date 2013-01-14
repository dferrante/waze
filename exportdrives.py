import requests, os, commands, time, datetime, sys, simplekml, numpy, re, math, collections
from gnosis.xml.objectify import make_instance

#configurables

outfile = 'drives.kml' # where final kml is written
kmlfolderrules = [
    ('morning', lambda x: x['startdate'].weekday() < 5 and x['startdate'].hour >= 8 and x['startdate'].hour <= 10 and x['distance'] >= 35 and x['distance'] <= 50),
    ('evening', lambda x: x['startdate'].weekday() < 5 and x['startdate'].hour >= 17 and x['startdate'].hour <= 19 and x['distance'] >= 35 and x['distance'] <= 50),
    ('long trips', lambda x: x['distance'] >= 150),
    ('other', lambda x: True),
] # use these to sort your drives so you can suss out your commute.  should always end with a catch-all that evals to True.
commutes = ['morning', 'evening'] # which of the above are regular routes
removegmlfiles = True # delete .gml files after downloading
timeslices = 5 # minutes per bucket to break up commutes by time, must be factor of 60


#waze API urls
session_url = "https://www.waze.com/Descartes-live/app/Session"
sessiondata_url = "https://www.waze.com/Descartes-live/app/Archive/Session"
sessionlist_url = "https://www.waze.com/Descartes-live/app/Archive/MyList"

def export(username, password):
    # login
    req = requests.post(session_url, data={'userName': username, 'password': password})
    authdict = dict([req.headers['set-cookie'].split(';')[0].split('=',1),]) if req.headers['set-cookie'] else {}
    if 'USERAUTH' not in authdict:
        print 'login failed, check credentials'
        sys.exit(255)

    # get sessions
    print 'getting sessions'
    sessionlist = []
    offset = 0
    sessions = requests.get(sessionlist_url, params={'count': 50, 'offset': offset}, cookies=authdict).json['archives']['objects']
    while sessions:
        sessionlist += [x for x in sessions]
        print 'got %s sessions' % len(sessionlist)
        offset += 50
        sessions = requests.get(sessionlist_url, params={'count': 50, 'offset': offset}, cookies=authdict).json['archives']['objects']
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
            print 'invalid session:', session['id']
            continue
        gmlfile = '%s.gml' % filename
        gfsfile = '%s.gfs' % filename
        kmlfile = '%s.kml' % filename
        if not os.path.exists(gmlfile) and not os.path.exists(kmlfile):
            data = requests.get(sessiondata_url, params={'id': session['id']}, cookies=authdict)
            try:
                gml = data.json['archiveSessions']['objects'][0]['data']
            except Exception, e:
                if 'code' in data.json and data.json['code'] == 101:
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
        else:
            print 'skipped %s' % session['id']


def colorspeed(speed):
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

def parsekmlname(name):
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
            'enddate': enddate, 'avgspeed': avgspeed, 'fmtname': fmtname, 'fmtname': fmtname}

    for folder, rule in kmlfolderrules:
        if rule(self):
            self['type'] = folder
            return self

def commutesplitbucket(folder, buckets, drivebuckets):
    averages = {}
    for fn in commutes:
        averages[fn] = folder.newfolder(name=fn, visibility=0)
        for bucket in sorted(list(set([x[3] for x in buckets.keys() if x[2] == fn]))):
            drivecount = len(drivebuckets[(bucket, fn)])
            avglength = round(numpy.mean([x[0] for x in drivebuckets[(bucket, fn)]]), 1)
            avgspeed = round(numpy.mean([x[1] for x in drivebuckets[(bucket, fn)]]), 1)
            foldername = '%s (%s drives/%smi/%smph)' % (bucket, drivecount, avglength, avgspeed)
            averages[fn+bucket] = averages[fn].newfolder(name=foldername, visibility=0)
            averages[fn+bucket+'-speed'] = averages[fn+bucket].newfolder(name='speed points', visibility=0)

    for k,v in buckets.iteritems():
        prevlinename, name, drivetype, bucket = k
        if drivetype not in commutes:
            continue
        avgspeed = numpy.mean([speed for speed, coords, length in v])
        length = numpy.mean([length for speed, coords, length in v])
        coords = sorted([x[1] for x in v], key=lambda x: len(x))[-1] #pick one with most coords
        makespeedline(averages[drivetype+bucket], averages[drivetype+bucket+'-speed'], name, coords, avgspeed, length)


def makespeedline(folder, spfolder, name, coords, speed, length):
    line = folder.newlinestring(coords=coords, name='%s - %smi - %smph' % (name, length, int(speed)))
    line.style.linestyle.width = 6
    line.style.linestyle.color = colorspeed(speed)
    line.tessellate = 1

    avgx = numpy.mean(map(float, [x[0] for x in coords]))
    avgy = numpy.mean(map(float, [x[1] for x in coords]))
    point = spfolder.newpoint(name='%s' % int(speed), coords=[(avgx, avgy),])
    point.iconstyle.icon.href = ''
    point.style.labelstyle.color = colorspeed(speed)
    point.style.labelstyle.scale = 0.75

    if not folder.visibility:
        line.visibility = 0
        point.visibility = 0

def colorize():
    kmlfiles = [parsekmlname(x) for x in os.listdir('.') if '.kml' in x and x != outfile]

    #parse kml files with gnosis
    drivedata = collections.defaultdict(list)
    linedata = collections.defaultdict(list)
    timebuckets = collections.defaultdict(list)
    drivetimebuckets = collections.defaultdict(list)
    weekbuckets = collections.defaultdict(list)
    driveweekbuckets = collections.defaultdict(list)
    monthbuckets = collections.defaultdict(list)
    drivemonthbuckets = collections.defaultdict(list)
    weekdaybuckets = collections.defaultdict(list)
    driveweekdaybuckets = collections.defaultdict(list)
    for kmlfile in kmlfiles:
        if kmlfile['distance'] < 1:
            continue

        kmldata = make_instance(open(kmlfile['filename']).read())
        try:
            lines = kmldata.Document.Folder.Placemark
            if not lines:
                continue
        except:
            continue

        prevlinename = 'start'
        weekbucketname = kmlfile['startdate'].strftime('%Y-%W')
        weekdaybucketname = kmlfile['startdate'].strftime('%A')
        monthbucketname = kmlfile['startdate'].strftime('%Y-%m')
        timebucketname = '%s:%02d%s' % (int(kmlfile['startdate'].strftime('%I')),
                                        math.floor(kmlfile['startdate'].minute/60.0*(60/timeslices))*timeslices,
                                        kmlfile['startdate'].strftime('%p').lower())
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
            length = round(int(data['length'])*0.000621371,1) #convert meters to miles
            speed = int(int(data['speed'])*0.621371) #convert kmh to mph
            coords = [tuple(x.split(',')) for x in l.LineString.coordinates.PCDATA.split()]
            kmlfile['lines'].append((name, coords, speed, length))

            if speed > 120:
                continue

            linedata[(prevlinename, name, kmlfile['type'])].append((speed, coords, length))
            linedata[(prevlinename, name, 'all')].append((speed, coords, length))
            timebuckets[(prevlinename, name, kmlfile['type'], timebucketname)].append((speed, coords, length))
            weekbuckets[(prevlinename, name, kmlfile['type'], weekbucketname)].append((speed, coords, length))
            weekdaybuckets[(prevlinename, name, kmlfile['type'], weekdaybucketname)].append((speed, coords, length))
            monthbuckets[(prevlinename, name, kmlfile['type'], monthbucketname)].append((speed, coords, length))
            prevlinename = name
        drivedata[kmlfile['type']].append(kmlfile)
        drivetimebuckets[(timebucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
        driveweekbuckets[(weekbucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
        driveweekdaybuckets[(weekdaybucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))
        drivemonthbuckets[(monthbucketname, kmlfile['type'])].append((kmlfile['distance'], kmlfile['avgspeed']))

    kml = simplekml.Kml()
    sortedfoldernames = [folder for folder, rule in kmlfolderrules]

    #drives
    drivefolder = kml.newfolder(name='drives', visibility=0)
    for fn in sortedfoldernames:
        subfolder = drivefolder.newfolder(name=fn, visibility=0)
        for drive in sorted(drivedata[fn], key=lambda x: x['startdate'], reverse=True):
            folder = subfolder.newfolder(name=drive['fmtname'], visibility=0)
            spfolder = folder.newfolder(name='speed labels')
            for line in drive['lines']:
                makespeedline(folder, spfolder, line[0], line[1], line[2], line[3])

    #drives by speed
    byspeedfolder = kml.newfolder(name='commutes by speed', visibility=0)
    for fn in commutes:
        subfolder = byspeedfolder.newfolder(name=fn, visibility=0)
        for drive in sorted(drivedata[fn], key=lambda x: x['avgspeed'], reverse=True):
            folder = subfolder.newfolder(name=drive['fmtname'], visibility=0)
            spfolder = folder.newfolder(name='speed labels')
            for line in drive['lines']:
                makespeedline(folder, spfolder, line[0], line[1], line[2], line[3])

    #drives by start time interval
    timeavgfolder = kml.newfolder(name='commutes by time', visibility=0)
    commutesplitbucket(timeavgfolder, timebuckets, drivetimebuckets)

    #drives by week
    weekavgfolder = kml.newfolder(name='commutes by week', visibility=0)
    commutesplitbucket(weekavgfolder, weekbuckets, driveweekbuckets)

    #drives by month
    monthavgfolder = kml.newfolder(name='commutes by month', visibility=0)
    commutesplitbucket(monthavgfolder, monthbuckets, drivemonthbuckets)

    #drives by day of week
    weekdayavgfolder = kml.newfolder(name='commutes by weekday', visibility=0)
    commutesplitbucket(weekdayavgfolder, weekdaybuckets, driveweekdaybuckets)

    #averages
    avgfolder = kml.newfolder(name='averages', visibility=0)
    averages = {}
    for fn in sortedfoldernames + ['all']:
        averages[fn] = avgfolder.newfolder(name=fn, visibility=0)
        averages[fn+'-speed'] = averages[fn].newfolder(name='speed labels', visibility=0)

    for k,v in linedata.iteritems():
        prevlinename, name, drivetype = k
        if len(v) <= 5 and drivetype in commutes:
            continue
        avgspeed = numpy.mean([speed for speed, coords, length in v])
        length = numpy.mean([length for speed, coords, length in v])
        coords = sorted([x[1] for x in v], key=lambda x: len(x))[-1] #pick one with most coords
        makespeedline(averages[drivetype], averages[drivetype+'-speed'], name, coords, avgspeed, length)

    #top speeds
    topspeedfolder = kml.newfolder(name='top speeds', visibility=0)
    topspeeds = {}
    for fn in sortedfoldernames + ['all']:
        topspeeds[fn] = topspeedfolder.newfolder(name=fn, visibility=0)
        topspeeds[fn+'-speed'] = topspeeds[fn].newfolder(name='speed labels', visibility=0)

    for k,v in linedata.iteritems():
        prevlinename, name, drivetype = k
        topspeed = max([speed for speed, coords, length in v])
        length = numpy.mean([length for speed, coords, length in v])
        coords = sorted([x[1] for x in v], key=lambda x: len(x))[-1] #pick one with most coords
        makespeedline(topspeeds[drivetype], topspeeds[drivetype+'-speed'], name, coords, topspeed, length)

    print 'writing', outfile
    kml.save(outfile)
    print 'wrote', outfile


if __name__ == '__main__':
    username = raw_input('username: ')
    password = raw_input('password: ')
    export(username, password)
    colorize()
