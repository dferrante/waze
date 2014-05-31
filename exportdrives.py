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
import simplekml
import simplejson
import string
import sys
import time
import pytz
import uuid
from gnosis.xml.objectify import make_instance
from tqdm import *
import logging as log
from django.template import Template, Context, loader
from django.conf import settings

log.basicConfig(level=log.INFO, format='%(levelname)s %(asctime)s %(message)s')
settings.configure(TEMPLATE_DIRS=('./templates',))

db = dataset.connect('sqlite:///waze.db')
#db = dataset.connect('sqlite:///:memory:')

#configurables
commutes = ['morning', 'evening'] # which of the above are regular routes
timeslices = 3 # minutes per bucket to break up commutes by time, must be factor of 60
recent_drives_count = 20

#waze API urls
GET_CSRF_URL = "https://www.waze.com/login/get"
SESSION_URL = "https://www.waze.com/login/create"
SESSION_LIST_URL = "https://www.waze.com/Descartes-live/app/Archive/List"
SESSON_DATA_URL = "https://www.waze.com/Descartes-live/app/Archive/Session"

def export(username, password):
    # login
    req = requests.get(GET_CSRF_URL)
    csrfdict = dict(req.cookies)
    csrfdict['editor_env'] = 'usa'
    headers = {'X-CSRF-Token': csrfdict['_csrf_token']}

    req = requests.post(SESSION_URL, data={'user_id': username, 'password': password}, cookies=csrfdict, headers=headers)

    try:
        authdict = dict(req.cookies)
    except:
        log.error('login failed, check credentials')
        sys.exit(255)

    # get sessions
    sessionlist = []
    for offset in range(0, 500, 50):
        json = requests.get(SESSION_LIST_URL, params={'count': 50, 'offset': offset}, cookies=authdict).json()
        sessions = json['archives']['objects']
        if not sessions:
            break
        sessionlist += [x for x in sessions]

    files = []
    for session in tqdm(sessionlist, 'converting to kml', leave=True):
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
            data = requests.get(SESSON_DATA_URL, params={'id': session['id']}, cookies=authdict)
            try:
                gml = data.json()['archiveSessions']['objects'][0]['data']
            except Exception, e:
                if 'code' in data.json() and data.json()['code'] == 101:
                    return
                continue
            f = open(gmlfile, 'w')
            f.write(gml)
            f.close()
            commands.getstatusoutput('ogr2ogr -f "KML" %s %s' % (kmlfile, gmlfile))
            os.remove(gfsfile)
            files.append(gmlfile)
    print
    for fn in sorted(files):
        log.info('retrieved: %s', fn[5:])

def colorspeed(speed, maxspeed=90.0, rgb=False):
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
    if rgb:
        color = '%02x%02x%02x' % (argb[3], argb[2], argb[1])
    else:
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

def commutesplitbucket(kmlname, drivebucket, drivetable, linetable, clustertable, linelimit):
    kmloutput = simplekml.Kml(visibility=0)
    log.info('calculating %s', kmlname)

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
            avgavgspeed = -1 if speeddiff >= -3 and  speeddiff <= 3 else avgspeed/float(avgdrivespeed)*55+15 if speeddiff > 0 else avgspeed/float(avgdrivespeed)*55-15
            makespeedline(averages[drivetype+bucket+'-avg'], averages[drivetype+bucket+'-avgspeed'], display_name, coords, avgavgspeed, length, speeddiff)

    log.info('writing %s', kmlname)
    kmloutput.save('%s.kml' % kmlname)

def drivesplitbucket(kmlname, drivetypes, drivetable, linetable, clustertable, sortkey, topcount=20, bottomcount=0):
    kmloutput = simplekml.Kml(visibility=0)
    log.info('calculating %s', kmlname)

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
                    if speeddiff > 3:
                        avgavgspeed, speedlabel = line['speed']/float(avgdrivespeed)*55+15, speeddiff
                    elif speeddiff < -3:
                        avgavgspeed, speedlabel = line['speed']/float(avgdrivespeed)*55-15, speeddiff
                    else:
                        avgavgspeed, speedlabel = -1, ""
                    makespeedline(avgfolder, avgspfolder, display_name, coords, avgavgspeed, line['length'], speedlabel)
                prevlinename = line['name']

    log.info('writing %s', kmlname)
    kmloutput.save('%s.kml' % kmlname)

def clusterspeedbucket(kmlname, drivetypes, drivetable, linetable, clustertable, speedkey):
    kmloutput = simplekml.Kml(visibility=0)
    log.info('calculating %s', kmlname)
    drives = {}
    for drivetype in drivetypes:
        drives[drivetype] = kmloutput.newfolder(name=drivetype, visibility=0)
        drives[drivetype+'-speed'] = drives[drivetype].newfolder(name='speed labels', visibility=0)

    for cluster in clustertable.all():
        if cluster['type'] != 'all' and cluster['count'] < 10:
            continue
        coords = [(x, y) for x, y in simplejson.loads(cluster['coords'])]
        makespeedline(drives[cluster['type']], drives[cluster['type']+'-speed'], cluster['name'], coords, cluster[speedkey], cluster['length'])

    log.info('writing %s', kmlname)
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

def clusterreport():
    kmlname = "clusters"
    kmloutput = simplekml.Kml(visibility=0)
    log.info('calculating %s', kmlname)
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

    log.info('writing %s', kmlname)
    kmloutput.save('%s.kml' % kmlname)

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
    drivetable = db['drives']
    linetable = db['lines']
    linecache = []

    clustertable = db['clusters']
    clusters = {}

    new = False
    for kfile in tqdm([x for x in sorted(os.listdir('./data')) if '.kml' in x], 'parsing kml', leave=True):
        if not drivetable.find_one(filename=kfile):
            drive = {'filename': kfile}
            drive['distance'] = float(drive['filename'][:-4].split('-')[-1][:-2])
            startdate = datetime.date(*map(int,drive['filename'][:-4].split('-')[:3]))
            startdate = startdate.replace(year=startdate.year+2000)

            if drive['distance'] < 1:
                continue

            kmldata = make_instance(open('./data/'+drive['filename']).read())
            try:
                lines = kmldata.Document.Folder.Placemark
                if not lines:
                    continue
            except:
                continue

            new = True

            lastline = 'start'
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
                    'prevline': lastline,
                    'speed': speed,
                    'length': round(int(data['length'])*0.000621371,1),
                }

                line['coords'] = simplejson.dumps([tuple(map(float, x.split(','))) for x in l.LineString.coordinates.PCDATA.split()])

                if hasattr(l, 'name') and getattr(l.name, 'PCDATA'):
                    name = l.name.PCDATA
                elif 'Name' in data and data['Name']:
                    name = data['Name']
                else:
                    name = ''

                name = string.replace(string.replace(name.strip(','), ',', ', ').strip(), '  ', ' ')

                line['name'] = name
                line['fullname'] = '%s - %s' % (lastline, name)

                linetime = map(int, data['start_time'].split(':'))
                date = pytz.utc.localize(datetime.datetime(startdate.year, startdate.month, startdate.day,
                                                           linetime[0], linetime[1], linetime[2]))

                linetime = map(int, data['end_time'].split(':'))
                enddate = pytz.utc.localize(datetime.datetime(startdate.year, startdate.month, startdate.day,
                                                           linetime[0], linetime[1], linetime[2]))

                timezone = pytz.timezone('US/Eastern')
                date = date.astimezone(timezone)
                enddate = enddate.astimezone(timezone)

                if lastline == 'start':
                    startdate = date

                if date < startdate:
                    date += datetime.timedelta(days=1)

                line['date'] = date
                linelist.append(line)
                lastline = line['name']

            firstline = 'start'
            for line in linelist:
                if line['name']:
                    firstline = line['name']
                    break

            drive['endpoints'] = "%s to %s" % (firstline, lastline)
            drive['startdate'] = startdate.replace(tzinfo=None)
            drive['enddate'] = enddate.replace(tzinfo=None)
            drive['triptime'] = (drive['enddate']-drive['startdate']).seconds
            drive['avgspeed'] = round(drive['distance']/(drive['triptime']/3600.0),1)
            drive['weekbucket'] = drive['startdate'].strftime('%Y-%W')
            drive['weekdaybucket'] = drive['startdate'].strftime('(%w) %A')
            drive['monthbucket'] = drive['startdate'].strftime('%Y-%m')
            drive['timebucket'] = '%s:%02d%s' % (int(drive['startdate'].strftime('%I')),
                                            math.floor(drive['startdate'].minute/60.0*(60/timeslices))*timeslices,
                                            drive['startdate'].strftime('%p').lower())
            drive['fmtname'] = '%s-%s (%smi/%smin/%smph)' % (drive['startdate'].strftime('%m/%d %I:%M%p'),
                                                             drive['enddate'].strftime('%I:%M%p'),
                                                             drive['distance'], int(drive['triptime'])/60, drive['avgspeed'])

            def namecheck(s, start, end):
                for l in linelist[start:end]:
                    if s.lower() in l['name'].lower():
                        return True
                return False

            if drive['startdate'] >= datetime.datetime(2013, 8, 5) and \
               drive['startdate'].weekday() < 5 and \
               drive['startdate'].hour >= 7 and \
               drive['startdate'].hour <= 10 and \
               drive['distance'] >= 47.3 and \
               drive['distance'] <= 57 and \
               namecheck('Studer', 0, 1) and \
               namecheck('CR-612', -7, None):
                drivetype = 'morning'
            elif drive['startdate'] >= datetime.datetime(2013, 8, 5) and \
                 drive['startdate'].weekday() < 5 and \
                 drive['startdate'].hour >= 16 and \
                 drive['startdate'].hour <= 19 and \
                 drive['distance'] >= 47.3 and \
                 drive['distance'] <= 57 and \
                 namecheck('Studer', -1, None) and \
                 namecheck('CR-612', 0, 7):
                drivetype = 'evening'
            else:
                drivetype = 'other'

            drive['type'] = drivetype
            driveid = drivetable.insert(drive)
            for line in linelist:
                line['drive'] = driveid
                line['type'] = drivetype
                line['cluster'] = repr(namecluster(line, clusters))

            linecache.extend(linelist)


    print
    log.info('loading line table')
    linetable.insert_many(linecache)

    log.info('clustering')
    if new:
        clusterrows = []
        for cname, cluster in clusters.items():
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

    log.info('building kmls')
    allfolders = [folder for folder, rule in kmlfolderrules] + ['all',]

    drivesplitbucket('drives', allfolders, drivetable, linetable, clustertable, 'date(startdate) desc', recent_drives_count)
    drivesplitbucket('drives by length', allfolders, drivetable, linetable, clustertable, 'distance desc', 10)
    drivesplitbucket('drives by avg speed', allfolders, drivetable, linetable, clustertable, 'avgspeed desc', 10, 10)
    drivesplitbucket('drives by total time', allfolders, drivetable, linetable, clustertable, 'avgspeed desc', 10, 10)
    commutesplitbucket('commutes by depart time', 'timebucket', drivetable, linetable, clustertable, 0)
    commutesplitbucket('commutes by week', 'weekbucket', drivetable, linetable, clustertable, 3)
    commutesplitbucket('commutes by month', 'monthbucket', drivetable, linetable, clustertable, 0)
    commutesplitbucket('commutes by weekday', 'weekdaybucket', drivetable, linetable, clustertable, 0)
    commutesplitbucket('commutes by distance', 'distancebucket', drivetable, linetable, clustertable, 0)
    clusterspeedbucket('averages', allfolders, drivetable, linetable, clustertable, 'speed')
    clusterspeedbucket('top speeds', allfolders, drivetable, linetable, clustertable, 'maxspeed')
    clusterspeedbucket('slow speeds', allfolders, drivetable, linetable, clustertable, 'minspeed')

    log.info('graphing')

    def movingaverage(values, window):
        weights = np.repeat(1.0, window)/window
        smas = np.convolve(values, weights, 'valid')
        return list(smas)
    movingavgfuncs = ((3, lambda x: movingaverage(x,3), False), (7, lambda x: movingaverage(x,7), False), (30, lambda x: movingaverage(x,30), True))

    apply_avg = lambda d, avgfunc: zip(*map(avgfunc, zip(*[(x['x'], x['y']) for x in d])))

    namefunc = lambda d, f: '%s (%smi)' % (secs_to_hms(d,f), d['distance'])
    secs_to_hms = lambda d, f: '%sh %sm %ss' % (d[f]/3600, (d[f]%3600)/60, d[f]%60) if d[f] > 3600 else '%sm %ss' % ((d[f]%3600)/60, d[f]%60)
    to_ms_ts = lambda d, f: float((to_dt(d[f])-datetime.datetime.utcfromtimestamp(0)).total_seconds()*1000)
    to_ms_ts_day = lambda d, f: float((to_dt_day(d[f])-datetime.datetime.utcfromtimestamp(0)).total_seconds()*1000)
    to_dt = lambda d: datetime.datetime.strptime(d, '%Y-%m-%d %H:%M:%S.%f')
    to_dt_day = lambda d: datetime.datetime.strptime(' '.join(['2014-01-01', d.split()[1]]), '%Y-%m-%d %H:%M:%S.%f')
    to_float_mins = lambda d, f: float(d[f])/60.0

    def get_drive_field_data(drivetype, field, fieldlimit, datafunc, namefunc, timefunc):
        data = []
        for drive in db.query('select * from drives where type="%s" order by date(startdate) asc' % drivetype):
            if fieldlimit:
                if fieldlimit[0] > int(drive[field]) or int(drive[field]) > fieldlimit[1]:
                    continue
                data.append({'x': timefunc(drive, 'startdate'), 'y': datafunc(drive, field), 'name': namefunc(drive, field)})
        return data

    def make_commute_graphs(drivetype):
        data = get_drive_field_data(drivetype, 'triptime', (0,3600*10), to_float_mins, namefunc, to_ms_ts)

        graph_data = [{
            'name': 'Drives',
            'type': 'scatter',
            'data': data,
        },]

        for days, avgfunc, vis in movingavgfuncs:
            graph_data.append({
                'name': '%s-Day Moving Average' % days,
                'type': 'line',
                'data': apply_avg(data, avgfunc),
                'marker': {'enabled': False},
                'states': {'hover': {'lineWidth': 0}},
                'enableMouseTracking': False,
                'visible': vis,
            })
        return graph_data

    morning_data = make_commute_graphs('morning')
    evening_data = make_commute_graphs('evening')

    depart_time_data = [
        {
            'name': 'Morning Departures',
            'type': 'scatter',
            'data': get_drive_field_data('morning', 'triptime', (0,3600*3), to_float_mins, namefunc, to_ms_ts_day),
        },
        {
            'name': 'Evening Departures',
            'type': 'scatter',
            'data': get_drive_field_data('evening', 'triptime', (0,3600*3), to_float_mins, namefunc, to_ms_ts_day),
        },
    ]

    def drive_graph_data(drive):
        categories = []
        data = []
        average_data = []
        average_data_diff = []
        average_speed = []
        speed_diffs = []

        for line in linetable.find(drive=drive['id'], order_by='date'):
            display_name = '%s %s (%smi)' % (line['date'].strftime('%H:%M'), line['name'], line['length'])
            categories.append(line['name'].split(',')[-1].strip() if line['name'] else '')

            speed = float(line['speed'])
            avgdrivespeed = float(clustertable.find_one(uuid=line['cluster'], type='all')['speed'])
            speeddiff = float(speed-avgdrivespeed)

            diffcolor = "#000000"
            if speeddiff >= 1:
                diffcolor = colorspeed(speed/avgdrivespeed*55+15, 90.0, True)
                avgcolor = diffcolor
            elif speeddiff <= -1:
                avgcolor = colorspeed(speed/avgdrivespeed*55-15, 90.0, True)
            else:
                avgcolor = colorspeed(speed, 90.0, True)
            diff_diff = speed-abs(speeddiff) if speeddiff >= 1 else avgdrivespeed-abs(speeddiff)

            tooltip = '<b>%s</b><br/>Speed: %smph<br/>Avg Speed: %smph<br/>Difference: %smph<br/>' % (display_name, int(speed), int(avgdrivespeed), int(speeddiff))

            data.append({'y': speed, 'name': tooltip, 'color': '#%s' % colorspeed(speed, 90.0, True)})
            average_speed.append({'y': avgdrivespeed, 'name': tooltip, 'color': '#%s' % colorspeed(avgdrivespeed, 90.0, True)})
            average_data.append({'y': diff_diff, 'name': tooltip, 'color': '#%s' % avgcolor})
            average_data_diff.append({'y': abs(speeddiff), 'name': tooltip, 'color': '#%s' % diffcolor})
            speed_diffs.append({'y': speeddiff, 'name': tooltip, 'color': '#%s' % avgcolor})

        series = {
            'categories': simplejson.dumps(categories),
            'data': simplejson.dumps([
                {'name': 'Speed', 'data': data, 'stack': 'speed', 'visible': False},
                {'name': 'Average Speed', 'data': average_speed, 'stack': 'avg', 'visible': False},
                {'name': 'Difference from Average', 'data': speed_diffs, 'stack': 'diff', 'visible': False},
                {'name': 'Average Difference', 'data': average_data_diff, 'stack': 'stackdiff', 'visible': True},
                {'name': 'Average Difference', 'data': average_data, 'stack': 'stackdiff', 'visible': True},
            ],),
            'average_data': simplejson.dumps(average_data),
            'drive': drive,
        }

        return series

    drive_graphs = []
    for drive in db.query('select * from drives order by date(startdate) desc limit 10'):
        drive_graphs.append(drive_graph_data(drive))

    fastest_commutes = []
    slowest_commutes = []
    for drivetype in commutes:
        for drive in db.query('select * from drives where type="%s" order by avgspeed desc limit 10' % drivetype):
            fastest_commutes.append(drive_graph_data(drive))
        for drive in db.query('select * from drives where type="%s" order by avgspeed asc limit 10' % drivetype):
            slowest_commutes.append(drive_graph_data(drive))


    def commute_split_graph(drivetype, drivebucket):
        avgspeeds = []
        avglengths = []
        avgtimes = []
        buckets = sorted(drivetable.distinct(drivebucket, type=drivetype))
        for bucket in buckets:
            bucket = bucket[drivebucket]
            bucketdrives = list(db.query('select id, distance, avgspeed from drives where type="%s" and %s="%s"' % (drivetype, drivebucket, bucket)))
            drivecount = len(bucketdrives)

            tooltip = '<b>%s</b><br/>%s Drives' % (bucket, drivecount)
            avglength = round(np.mean([x['distance'] for x in bucketdrives]), 1)
            avgspeed = round(np.mean([x['avgspeed'] for x in bucketdrives]), 1)

            avglengths.append({'y': avglength, 'name': tooltip})
            avgspeeds.append({'y': avgspeed, 'name': tooltip})
            avgtimes.append({'y': round((avglength/avgspeed)*60, 1), 'name': tooltip})

        series = {
            'categories': simplejson.dumps([b[drivebucket] for b in buckets]),
            'data': simplejson.dumps([
                {'name': 'Average Time', 'data': avgtimes, 'stack': 'time', 'visible': True},
                {'name': 'Average Length', 'data': avglengths, 'stack': 'length', 'visible': False},
                {'name': 'Average Speed', 'data': avgspeeds, 'stack': 'speed', 'visible': False},
            ],),
            'title': '%s Commute by %s' % (drivetype.title(), drivebucket.title()),
            'target': '%s-%s' % (drivebucket, drivetype),
        }

        return series

    bucket_graphs = [
        commute_split_graph('morning', 'weekbucket'),
        commute_split_graph('evening', 'weekbucket'),
        commute_split_graph('morning', 'monthbucket'),
        commute_split_graph('evening', 'monthbucket'),
        commute_split_graph('morning', 'weekdaybucket'),
        commute_split_graph('evening', 'weekdaybucket'),
    ]

    t = loader.get_template('commutes.html')
    c = Context({
        'morning_data': simplejson.dumps(morning_data),
        'evening_data': simplejson.dumps(evening_data),
        'depart_time_data': simplejson.dumps(depart_time_data),
        'drive_graphs': drive_graphs,
        'fastest_commutes': fastest_commutes,
        'slowest_commutes': slowest_commutes,
        'bucket_graphs': bucket_graphs,
    })
    with open('report.html', 'w') as f:
        f.write(t.render(c))
    commands.getoutput('/usr/bin/google-chrome "file:///home/mach5/Documents/waze/report.html"')


if __name__ == '__main__':
    username = raw_input('username: ')
    password = raw_input('password: ')
    export(username, password)
    buildreports()
