from bokeh import plotting
import collections
import commands
import datetime
import dataset
import ipdb
import math
import numpy as np
import os
import random
import re
import requests
from rpy2.robjects.packages import importr
import rpy2.robjects as robjects
import simplekml
import simplejson
import string
import sys
import time
import uuid
from gnosis.xml.objectify import make_instance

db = dataset.connect('sqlite:///waze.db')
#db = dataset.connect('sqlite:///:memory:')
pclib = importr("princurve")

#configurables
outfile = 'drives.kml' # where final kml is written
kmlfolderrules = [
    ('morning', lambda x: x['startdate'] >= datetime.datetime(2013, 8, 5) and x['startdate'].weekday() < 5 and x['startdate'].hour >= 7 and x['startdate'].hour <= 10 and x['distance'] >= 45 and x['distance'] <= 52),
    ('evening', lambda x: x['startdate'] >= datetime.datetime(2013, 8, 5) and x['startdate'].weekday() < 5 and x['startdate'].hour >= 16 and x['startdate'].hour <= 19 and x['distance'] >= 46 and x['distance'] <= 52),
    ('other', lambda x: True),
] # use these to sort your drives so you can suss out your commute.  should always end with a catch-all that evals to True.
commutes = ['morning', 'evening'] # which of the above are regular routes
removegmlfiles = True # delete .gml files after downloading
timeslices = 3 # minutes per bucket to break up commutes by time, must be factor of 60
top_and_bottom_ranking_limit = 10
recent_drives_count = 10

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


def colorspeed(speed, maxspeed=90.0):
    if speed == -1: # special case
        return '66000000'

    alpha = 200
    speed = speed-10
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
    try:
        dates = [datetime.datetime.strptime(d, '%Y-%m-%d %H:%M:%S.%f') for d in dates]
    except TypeError:
        pass
    avgseconds = np.mean([date.hour * 60 * 60 + date.minute * 60 + date.second for date in dates])
    return '%s:%s' % (int(avgseconds / 3600), int(avgseconds%60))

def haversine(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees)
    """
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    m = 6367 * c * 1000
    return m

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
        avgspeed = round(distance/(triptime/60.0),1)
        weekbucketname = startdate.strftime('%Y-%W')
        weekdaybucketname = startdate.strftime('(%w) %A')
        monthbucketname = startdate.strftime('%Y-%m')
        timebucketname = '%s:%02d%s' % (int(startdate.strftime('%I')),
                                        math.floor(startdate.minute/60.0*(60/timeslices))*timeslices,
                                        startdate.strftime('%p').lower())

        fmtname = '%s-%s (%smi/%smin/%smph)' % (startdate.strftime('%m/%d %I:%M%p'), enddate.strftime('%I:%M%p'), distance, triptime, avgspeed)
        self = {'filename': name, 'distance': distance, 'startdate': startdate,
                'enddate': enddate, 'avgspeed': avgspeed, 'fmtname': fmtname, 'triptime': triptime,
                'weekbucket': weekbucketname, 'weekdaybucket': weekdaybucketname,
                'monthbucket': monthbucketname, 'timebucket': timebucketname,
                'distancebucket': str(distance),}

        for folder, rule in kmlfolderrules:
            if rule(self):
                self['type'] = folder
                return self
    except:
        return False

def commutesplitbucket(kmlname, drivebucket, drivetable, linetable, clustertable, linelimit):
    kmloutput = simplekml.Kml(visibility=0)
    print 'calculating', kmlname

    averages = {}
    bucketclusters = collections.defaultdict(list)
    for drivetype in commutes:
        averages[drivetype] = kmloutput.newfolder(name=drivetype, visibility=0)
        averages[drivetype+'-avg'] = kmloutput.newfolder(name='%s vs. avg' % drivetype, visibility=0)
        for bucket in sorted(drivetable.distinct(drivebucket, type=drivetype)):
            bucket = bucket[drivebucket]
            bucketdrives = list(db.query('select id, distance, avgspeed from drives where type="%s" and %s="%s"' % (drivetype, drivebucket, bucket)))
            drivecount = len(bucketdrives)
            if drivecount < linelimit:
                continue
            avglength = round(np.mean([x['distance'] for x in bucketdrives]), 1)
            avgspeed = round(np.mean([x['avgspeed'] for x in bucketdrives]), 1)
            avgtime = round((avglength/avgspeed)*60, 1)
            foldername = '%s (%s drives/%smi/%smph/%smin)' % (bucket, drivecount, avglength, avgspeed, avgtime)
            averages[drivetype+bucket] = averages[drivetype].newfolder(name=foldername, visibility=0)
            averages[drivetype+bucket+'-speed'] = averages[drivetype+bucket].newfolder(name='speed points', visibility=0)
            averages[drivetype+bucket+'-avg'] = averages[drivetype+'-avg'].newfolder(name=foldername, visibility=0)
            averages[drivetype+bucket+'-avgspeed'] = averages[drivetype+bucket+'-avg'].newfolder(name='speed points', visibility=0)

            for drive in bucketdrives:
                for line in linetable.find(drive=drive['id']):
                    bucketclusters[(line['cluster'], bucket, drivetype)].append(line)

    for (cmatch, bucket, drivetype), lines in bucketclusters.iteritems():
        if clustertable.find_one(uuid=cmatch, type=drivetype)['count'] < 10:
            continue
        avgspeed = np.mean([l['speed'] for l in lines])
        length = np.mean([l['length'] for l in lines])
        avgdate = averagetime([l['date'] for l in lines])
        coords = max([simplejson.loads(l['coords']) for l in lines], key=lambda y: len(y)) #pick one with most coords
        display_name = '%s %s (%s)' % (avgdate, ', '.join(list(set([l['name'] for l in lines]))), len(lines))
        makespeedline(averages[drivetype+bucket], averages[drivetype+bucket+'-speed'], display_name, coords, avgspeed, length)

        avgdrivespeed = clustertable.find_one(uuid=cmatch, type='all')['speed']
        if avgdrivespeed > 0:
            speeddiff = int(avgspeed-avgdrivespeed)
            avgavgspeed = -1 if speeddiff == 0 else avgspeed/float(avgdrivespeed)*55+15 if speeddiff > 0 else avgspeed/float(avgdrivespeed)*55-15
            makespeedline(averages[drivetype+bucket+'-avg'], averages[drivetype+bucket+'-avgspeed'], display_name, coords, avgavgspeed, length, speeddiff)

    print 'writing', kmlname
    kmloutput.save('%s.kml' % kmlname)

def drivesplitbucket(kmlname, drivetypes, drivetable, linetable, clustertable, sortkey, topcount=20, bottomcount=0):
    kmloutput = simplekml.Kml(visibility=0)
    print 'calculating', kmlname

    for drivetype in drivetypes:
        subfolder = kmloutput.newfolder(name=drivetype, visibility=0)
        avgsubfolder = kmloutput.newfolder(name="%s vs. avg" % drivetype, visibility=0)

        if drivetype == 'all':
            query = "select * from drives order by %s limit %s" % (sortkey, topcount)
        else:
            query = "select * from drives where type='%s' order by %s limit %s" % (drivetype, sortkey, topcount)
        drivelist = [d for d in db.query(query)]

        if bottomcount:
            #awful
            revquery = re.sub(' asc ', ' desc ', query) if ' asc ' in query else re.sub(' desc ', ' asc ', query)
            revdrivelist = [d for d in db.query(revquery)]
            drivelist += revdrivelist

        for drive in drivelist:
            folder = subfolder.newfolder(name=drive['fmtname'], visibility=0)
            spfolder = folder.newfolder(name='speed labels')
            avgfolder = avgsubfolder.newfolder(name=drive['fmtname'], visibility=0)
            avgspfolder = avgfolder.newfolder(name='speed labels')

            prevlinename = 'start'
            for line in linetable.find(drive=drive['id'], order_by='date'):
                display_name = '%s %s' % (line['date'].strftime('%H:%M'), line['name'])
                coords = simplejson.loads(line['coords'])
                makespeedline(folder, spfolder, display_name, coords, line['speed'], line['length'])
                avgdrivespeed = clustertable.find_one(uuid=line['cluster'], type='all')['speed']
                if avgdrivespeed > 0:
                    speeddiff = int(line['speed']-avgdrivespeed)
                    if speeddiff == 0:
                        avgavgspeed, speedlabel = -1, ""
                    elif speeddiff > 0:
                        avgavgspeed, speedlabel = line['speed']/float(avgdrivespeed)*55+15, speeddiff
                    else:
                        avgavgspeed, speedlabel = line['speed']/float(avgdrivespeed)*55-15, speeddiff

                    makespeedline(avgfolder, avgspfolder, display_name, coords, avgavgspeed, line['length'], speedlabel)
                prevlinename = line['name']

    print 'writing', kmlname
    kmloutput.save('%s.kml' % kmlname)

def clusterspeedbucket(kmlname, drivetypes, drivetable, linetable, clustertable, speedkey):
    kmloutput = simplekml.Kml(visibility=0)
    print 'calculating', kmlname
    drives = {}
    for drivetype in drivetypes:
        drives[drivetype] = kmloutput.newfolder(name=drivetype, visibility=0)
        drives[drivetype+'-speed'] = drives[drivetype].newfolder(name='speed labels', visibility=0)

    for cluster in clustertable.all():
        if cluster['type'] != 'all' and cluster['count'] < 10:
            continue
        coords = [(x, y) for x, y in simplejson.loads(cluster['coords'])]
        makespeedline(drives[cluster['type']], drives[cluster['type']+'-speed'], cluster['name'], coords, cluster[speedkey], cluster['length'])

    print 'writing', kmlname
    kmloutput.save('%s.kml' % kmlname)

def makespeedline(folder, spfolder, name, coords, speed, length, speedlabel=None, maxspeed=90.0):
    line = folder.newlinestring(coords=coords, name='%s - %smi - %smph' % (name, length, int(speed)))
    line.style.linestyle.width = 6
    line.style.linestyle.color = colorspeed(speed, maxspeed)
    line.tessellate = 1

    if not folder.visibility:
        line.visibility = 0

    avgx = np.mean(map(float, [x[0] for x in coords]))
    avgy = np.mean(map(float, [x[1] for x in coords]))

    speedlabel = '%s' % (speedlabel if speedlabel is not None else int(speed))
    if speedlabel:
        point = spfolder.newpoint(name=speedlabel, coords=[(avgx, avgy),])
        point.iconstyle.icon.href = ''
        point.style.labelstyle.color = colorspeed(speed, maxspeed)
        point.style.labelstyle.scale = 0.85

        if not folder.visibility:
            point.visibility = 0

def greatcirclecluster(line, clusters):
    coords = simplejson.loads(line['coords'])
    startpt = coords[0]
    endpt = coords[-1]
    max_distance = 50
    cmatch = False

    for cname, (cstart, cend, count, x) in sorted(clusters.iteritems(), key=lambda c: c[1][2]):
        sdist = haversine(startpt[0], startpt[1], cstart[0], cstart[1])
        if sdist <= max_distance:
            edist = haversine(endpt[0], endpt[1], cend[0], cend[1])
            if edist <= max_distance:
                cmatch = cname
                break
    if cmatch:
        clusters[cmatch] = (
            ((clusters[cmatch][0][0] + startpt[0]) / 2, (clusters[cmatch][0][1] + startpt[1]) / 2),
            ((clusters[cmatch][1][0] + endpt[0]) / 2, (clusters[cmatch][1][1] + endpt[1]) / 2),
            clusters[cmatch][2] + 1,
            max(clusters[cmatch][3], coords)
        )
    else:
        cmatch = str(uuid.uuid4())
        clusters[cmatch] = (startpt, endpt, 1, coords)

    return cmatch

def namecluster(line, clusters):
    coords = simplejson.loads(line['coords'])
    startpt = coords[0]
    endpt = coords[-1]
    cmatch = (line['prevline'], line['name'])

    if cmatch in clusters:
        if line['type'] in clusters[cmatch]['speeds']:
            clusters[cmatch]['speeds'][line['type']].append(line['speed'])
        else:
            clusters[cmatch]['speeds'][line['type']] = [line['speed'],]

        clusters[cmatch]['names'].add(line['name'])

        clusters[cmatch] = {
            'startpt': ((clusters[cmatch]['startpt'][0] + startpt[0]) / 2, (clusters[cmatch]['startpt'][1] + startpt[1]) / 2),
            'endpt': ((clusters[cmatch]['endpt'][0] + endpt[0]) / 2, (clusters[cmatch]['endpt'][1] + endpt[1]) / 2),
            'count': clusters[cmatch]['count'] + 1,
            'coords': max(clusters[cmatch]['coords'], coords),
            'names': clusters[cmatch]['names'],
            'speeds': clusters[cmatch]['speeds'],
            'lengths': clusters[cmatch]['lengths'] + [line['length'],],
        }
    else:
        clusters[cmatch] = {
            'startpt': startpt,
            'endpt': endpt,
            'count': 1,
            'coords': coords,
            'speeds': {line['type']: [line['speed'],]},
            'names': set([line['name'],]),
            'lengths': [line['length'],],
        }

    return cmatch

def principalcurve(coords):
    try:
        array = [y for x in sorted(coords, key=lambda x: x[0]) for y in x]
        matrix = robjects.r.matrix(robjects.FloatVector(array),ncol=2)
        pcurve = pclib.principal_curve(matrix)
        coords = zip(*2*[iter(pcurve[0])])
    except:
        pass
    return coords

def buildreports():
    print 'starting report'
    drivetable = db['drives']
    linetable = db['lines']

    clustertable = db['clusters']
    clusters = {}
    #for cluster in clustertable.all():
        #clusters[cluster['uuid']] = cluster

    new = False
    for kfile in [x for x in sorted(os.listdir('./data')) if '.kml' in x]:
        if not drivetable.find_one(filename=kfile):
            drive = parsekmlname(kfile)

            if not drive:
                continue

            if drive['distance'] < 1:
                continue

            kmldata = make_instance(open('./data/'+drive['filename']).read())
            try:
                lines = kmldata.Document.Folder.Placemark
                if not lines:
                    continue
            except:
                continue

            driveid = drivetable.insert(drive)
            new = True
            print 'parsing %s' % kfile

            prevline = 'start'
            linelist = []
            for l in lines:
                try:
                    data = datadict(l.ExtendedData.SchemaData.SimpleData)
                except:
                    continue

                status = data['status']
                if status != 'OK':
                    continue

                speed = int(int(data['speed'])*0.621371) #convert kmh to mph
                if speed > 110 or speed <= 0:
                    continue

                line = {
                    'drive': driveid,
                    'type': drive['type'],
                    'prevline': prevline,
                    'speed': speed,
                    'length': round(int(data['length'])*0.000621371,1),
                }

                line['coords'] = simplejson.dumps([tuple(map(float, x.split(','))) for x in l.LineString.coordinates.PCDATA.split()])

                name = data['Name'].strip(',') if 'Name' in data and data['Name'] else ''
                line['name'] = string.replace(string.replace(name, ',', ', ').strip(), '  ', ' ')
                line['fullname'] = '%s - %s' % (prevline, name)

                linetime = map(int, [x for x in data['start_time'].split(':')])
                if linetime[0] < 0:
                    linetime[0] += 24
                line['date'] = datetime.datetime(drive['startdate'].year, drive['startdate'].month, drive['startdate'].day, linetime[0], linetime[1], linetime[2])
                line['date'] += datetime.timedelta(hours=-5)
                if line['date'] < drive['startdate']:
                    line['date'] += datetime.timedelta(days=1)

                line['cluster'] = repr(namecluster(line, clusters))
                linelist.append(line)
                prevline = line['name']

            linetable.insert_many(linelist)

    if new:
        print 'adding cluster to db'
        clusterrows = []
        for cname, cluster in clusters.iteritems():
            for drivetype in cluster['speeds'].keys():
                speedarray = np.array(cluster['speeds'][drivetype])
                clusterrows.append({
                    'uuid': repr(cname),
                    'speed': int(speedarray.mean()),
                    'minspeed': int(speedarray.min()),
                    'maxspeed': int(speedarray.max()),
                    'startpt': simplejson.dumps(cluster['startpt']),
                    'endpt': simplejson.dumps(cluster['endpt']),
                    'coords': simplejson.dumps(cluster['coords']),
                    'count': len(speedarray),
                    'type': drivetype,
                    'speeds': simplejson.dumps(cluster['speeds']),
                    'length': round(np.array(cluster['lengths']).mean(), 2),
                    'name': '|'.join(cluster['names']),
                })

            speedarray = np.array([speed for dt in cluster['speeds'].values() for speed in dt])
            clusterrows.append({
                'uuid': repr(cname),
                'speed': int(speedarray.mean()),
                'minspeed': int(speedarray.min()),
                'maxspeed': int(speedarray.max()),
                'startpt': simplejson.dumps(cluster['startpt']),
                'endpt': simplejson.dumps(cluster['endpt']),
                'coords': simplejson.dumps(cluster['coords']),
                'count': cluster['count'],
                'type': 'all',
                'speeds': simplejson.dumps(cluster['speeds']),
                'length': round(np.array(cluster['lengths']).mean(), 2),
                'name': '|'.join(cluster['names']),
            })
        clustertable.delete()
        clustertable.insert_many(clusterrows)


    print 'building kmls'
    allfolders = [folder for folder, rule in kmlfolderrules] + ['all',]


    drivesplitbucket('drives', allfolders, drivetable, linetable, clustertable, 'date(startdate) desc', recent_drives_count)
    drivesplitbucket('drives by length', allfolders, drivetable, linetable, clustertable, 'distance desc', 10)
    drivesplitbucket('drives by avg speed', allfolders, drivetable, linetable, clustertable, 'avgspeed desc', 10, 10)
    drivesplitbucket('drives by total time', allfolders, drivetable, linetable, clustertable, 'avgspeed desc', 10, 10)
    commutesplitbucket('commutes by depart time', 'timebucket', drivetable, linetable, clustertable, 5)
    commutesplitbucket('commutes by week', 'weekbucket', drivetable, linetable, clustertable, 3)
    commutesplitbucket('commutes by month', 'monthbucket', drivetable, linetable, clustertable, 0)
    commutesplitbucket('commutes by weekday', 'weekdaybucket', drivetable, linetable, clustertable, 0)
    #commutesplitbucket('commutes by distance', 'distancebucket', drivetable, linetable, clustertable)
    clusterspeedbucket('averages', allfolders, drivetable, linetable, clustertable, 'speed')
    clusterspeedbucket('top speeds', allfolders, drivetable, linetable, clustertable, 'maxspeed')
    clusterspeedbucket('slow speeds', allfolders, drivetable, linetable, clustertable, 'minspeed')

    kmlname = "clusters"
    kmloutput = simplekml.Kml(visibility=0)
    print 'calculating', kmlname
    drives = {}
    for drivetype in allfolders:
        drives[drivetype] = kmloutput.newfolder(name=drivetype, visibility=0)

    countbuckets = {}
    for cluster in clustertable.find(order_by='-count'):
        clusterfolder = drives[cluster['type']].newfolder(name='%s: %s' % (cluster['count'], cluster['uuid']), visibility=0)
        clusterspeedfolder = clusterfolder.newfolder(name='speed labels', visibility=0)
        randomcolor = random.randint(1, 90)
        for line in linetable.find(cluster=cluster['uuid']):
            coords = [(x, y) for x, y in simplejson.loads(line['coords'])]
            makespeedline(clusterfolder, clusterspeedfolder, line['name'], coords, randomcolor, line['length'])

    print 'writing', kmlname
    kmloutput.save('%s.kml' % kmlname)

    #plotting.output_file("commutes.html", title="candlestick.py example",
                #js="relative", css="relative")
    #plotting.hold()

    #for drivetype in commutes:
        #linedata = collections.defaultdict(list)
        #for drive in db.query('select * from drives where type="%s" order by date(startdate) asc' % drivetype):
            #if drive['triptime'] > 100:
                #continue
            #linedata['date'].append(datetime.datetime.strptime(drive['startdate'], '%Y-%m-%d %H:%M:%S.%f').strftime('%Y-%m-%d'))
            #linedata['triptime'].append(drive['triptime'])

        #print np.array(linedata['date'], dtype='datetime64[M]').astype('int')
        #plotting.line(np.array(linedata['date'], dtype='datetime64').astype('int')*100000000, linedata['triptime'],
             #x_axis_type="datetime", color='#A6CEE3', tools="pan,zoom,resize")

        #plotting.curplot().title = "%s commute historical triptime" % drivetype
        #plotting.xaxis().major_label_orientation = math.pi/4
        #plotting.grid().grid_line_alpha=0.3

        #plotting.figure()

    #plotting.show()  # open a browser


if __name__ == '__main__':
    username = raw_input('username: ')
    password = raw_input('password: ')
    export(username, password)
    buildreports()
