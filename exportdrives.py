import requests, os, commands, time, datetime, sys

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
        if not os.path.exists(gmlfile):
            data = requests.get(sessiondata_url, params={'id': session['id']}, cookies=authdict)
            try:
                gml = data.json['archiveSessions']['objects'][0]['data']
            except Exception, e:
                if 'code' in data.json and data.json['code'] == 101:
                    print 'error:', data.json['details']
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

if __name__ == '__main__':
    username = raw_input('username: ')
    password = raw_input('password: ')
    export(username, password)
