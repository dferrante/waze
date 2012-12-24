import requests, os, commands, time, datetime, sys, simplekml, numpy, re
from gnosis.xml.objectify import make_instance


outfile = 'drives.kml'
kmlfolderrules = [
    ('morning', lambda x: x['startdate'].weekday() < 5 and x['startdate'].hour >= 8 and x['startdate'].hour <= 10),
    ('evening', lambda x: x['startdate'].weekday() < 5 and x['startdate'].hour >= 17 and x['startdate'].hour <= 19),
    ('other', lambda x: True),
]

session_url = "https://www.waze.com/Descartes-live/app/Session"
sessiondata_url = "https://www.waze.com/Descartes-live/app/Archive/Session"
sessionlist_url = "https://www.waze.com/Descartes-live/app/Archive/MyList"

def export(username, password):
    req = requests.post(session_url, data={'userName': username, 'password': password})
    authdict = dict([req.headers['set-cookie'].split(';')[0].split('=',1),])
    if 'USERAUTH' not in authdict:
        print 'login failed, check credentials'
        sys.exit(255)
    print 'logged in'

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
                    print 'the rest are invalid, quitting'
                    return
                else:
                    print 'error:', data.url, data.content
                continue
            f = open(gmlfile, 'w')
            f.write(gml)
            f.close()
            commands.getstatusoutput('ogr2ogr -f "KML" %s %s' % (kmlfile, gmlfile))
            os.remove(gmlfile)
            os.remove(gfsfile)
            c += 1
            print 'wrote %s (%s/%s)' % (gmlfile, c, len(sessionlist))
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
    distance = name[:-4].split('-')[-1]
    startdate = datetime.datetime(2000+sd[0], sd[1], sd[2], st[0], st[1])
    enddate = datetime.datetime(2000+ed[0], ed[1], ed[2], et[0], et[1])
    self = {'filename': name, 'distance': distance, 'startdate': startdate, 'enddate': enddate}

    for folder, rule in kmlfolderrules:
        if rule(self):
            self['folder'] = folder
            return self

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

def colorize():
    kmlfiles = sorted([parsekmlname(x) for x in os.listdir('.') if '.kml' in x and x != outfile], key=lambda x: x['startdate'])
    sortedfoldernames = [folder for folder, rule in kmlfolderrules]

    kml = simplekml.Kml()

    drivefolder = kml.newfolder(name='drives')
    drive = {}
    for fn in sortedfoldernames:
        drive[fn] = drivefolder.newfolder(name=fn)

    linedata = {}
    for kmlfile in kmlfiles:
        subfolder = drive[kmlfile['folder']]

        if float(kmlfile['distance'][:-2]) < 1:
            continue

        kmldata = make_instance(open(kmlfile['filename']).read())
        try:
            lines = kmldata.Document.Folder.Placemark
            if not lines:
                continue
        except:
            continue

        folder = subfolder.newfolder(name='%s %s' % (kmlfile['startdate'].strftime('%m/%d/%y %H:%M'), kmlfile['distance']))
        print kmlfile['startdate'].strftime('%m/%d/%y %H:%M'), kmlfile['distance']
        spfolder = folder.newfolder(name='speed labels')
        prevlinename = 'start'
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

            if speed > 120:
                continue

            if (prevlinename, name, kmlfile['folder']) in linedata:
                linedata[(prevlinename, name, kmlfile['folder'])].append((speed, coords, length))
            else:
                linedata[(prevlinename, name, kmlfile['folder'])] = [(speed, coords, length),]
            prevlinename = name

            makespeedline(folder, spfolder, name, coords, speed, length)

    avgfolder = kml.newfolder(name='averages')
    averages = {}
    for fn in sortedfoldernames:
        averages[fn] = avgfolder.newfolder(name=fn)
        averages[fn+'-speed'] = averages[fn].newfolder(name='speed labels')

    topspeedfolder = kml.newfolder(name='top speeds')
    topspeeds = {}
    for fn in sortedfoldernames:
        topspeeds[fn] = topspeedfolder.newfolder(name=fn)
        topspeeds[fn+'-speed'] = topspeeds[fn].newfolder(name='speed labels')

    for k,v in linedata.iteritems():
        prevlinename, name, folder = k
        speedlist = [speed for speed, coords, length in v]
        avgspeed = numpy.mean(speedlist)
        topspeed = max(speedlist)
        coords = sorted([x[1] for x in v], key=lambda x: len(x))[-1] #pick one with most coords
        length = numpy.mean([length for speed, coords, length in v])
        makespeedline(averages[folder], averages[folder+'-speed'], name, coords, avgspeed, length)
        makespeedline(topspeeds[folder], topspeeds[folder+'-speed'], name, coords, topspeed, length)

    kml.save(outfile)
    print 'wrote', outfile


if __name__ == '__main__':
    username = raw_input('username: ')
    password = raw_input('password: ')
    export(username, password)
    colorize()
