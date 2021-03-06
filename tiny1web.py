import os, subprocess, base64, json, traceback, time, math, random, sys
import tornado.ioloop
import tornado.websocket
import tornado.web
from tornado.options import define, options, parse_command_line
import euclid

define("port",default=8888,type=int)
define("branch",default="master")
define("access",type=str,multiple=True)
define("local",type=bool)

define("welcome",type=str)
define("fps",default=8,type=int)
define("origin",default="http://williame.github.com",type=str)

num_models = 8
player_size = 0.025
epslion = 0.0001
forward = euclid.Quaternion(0.,0.,0.,-1.)

def set_fps(fps):
    global ticks_per_sec
    global roll_speed, max_roll_speed
    global pitch_speed, max_pitch_speed
    global yaw_speed, max_yaw_speed
    global speed, max_speed
    global shot_length, max_shot_age
    ticks_per_sec = fps
    base_speed = (.0015*8)/ticks_per_sec
    base_max = (.05*8)/ticks_per_sec
    roll_speed = base_speed
    max_roll_speed = base_max
    pitch_speed = base_speed
    max_pitch_speed = base_max
    yaw_speed = base_speed
    max_yaw_speed = base_max
    speed = base_speed/2
    max_speed = base_max/2
    shot_length = (.03*8)/ticks_per_sec
    max_shot_age = (ticks_per_sec/2)+1
    print "speed",speed*ticks_per_sec
    print "shot range",shot_length*ticks_per_sec

def point_distance(a,b):
    return math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2 + (a.y-b.y)**2)

def line_to_point(p,a,b): 
    v, w = b-a, p-a
    c1 = w.dot(v)
    if c1<=0: return sys.maxint
    c2 = v.dot(v)
    if c2 <= c1: return sys.maxint
    return point_distance(p,a + (c1/c2) * v)

class Shot:
    seq = 100
    def __init__(self,client):
        self.seq = Shot.seq
        Shot.seq += 1
        self.client = client
        self.pos = client.pos.copy()
        self.vec = ((client.rot * forward) * client.rot.conjugated())
        self.vec = euclid.Vector3(self.vec.x,self.vec.y,self.vec.z).normalized() * shot_length
        self.age = max_shot_age
    def tick(self,clients):
        nearest, distance = None, sys.maxint
        end = self.pos+self.vec
        for client in clients:
            d = line_to_point(client.pos,self.pos,end)
            #if d < distance: print "shoot",self.client.name,client.name,d
            if d < distance and d <= player_size:
                nearest, distance = client, d
        self.pos = end
        self.age -= 1
        return nearest, distance

class Game:
    def __init__(self):
        self.seq = 1
        self.clients = set()
    def now(self):
        return time.time()-self.start_time
    def add_client(self,client):
        if not self.clients:
            #self.seq = 1
            self.tick_length = 1./ticks_per_sec
            self.start_time = time.time()
            self.tick = 0
            self.shots = []
            self.ticker = tornado.ioloop.PeriodicCallback(self.run,1000/(ticks_per_sec*2))
            self.ticker.start()
        client.name = "player%d"%self.seq
        client.time = self.tick
        client.model = random.randint(0,num_models-1)
        self.seq += 1
        message = json.dumps({
            "joining":{
                "name":client.name,
                "model":client.model,
                "time":client.time,
                "pos":(client.pos.x,client.pos.y,client.pos.z),
                "rot":(client.rot.x,client.rot.y,client.rot.z,client.rot.w),
                "speed":client.speed,
            },
        })
        for competitor in self.clients:
            competitor.write_message(message)
        self.clients.add(client)
        message = {
            "welcome":{
                "name":client.name,
                "tick_length":1000/ticks_per_sec,
                "start_time":self.start_time*1000,
                "time_now":self.now()*1000,
                "players":[{
                    "name":c.name,
                    "model":c.model,
                    "time":c.time,
                    "pos":(c.pos.x,c.pos.y,c.pos.z),
                    "rot":(c.rot.x,c.rot.y,c.rot.z,c.rot.w),
                    "speed":c.speed,
                } for c in self.clients],
            },
        }
        client.write_message(json.dumps(message))
    def remove_client(self,client,reason):
        if client in self.clients:
            message = json.dumps({"leaving":client.name,"reason":reason,"killed_by":client.killed_by})
            for c in self.clients:
                c.write_message(message)
            self.clients.remove(client)
            if not self.clients:
                self.ticker.stop() 
        if hasattr(client,"name"):
            print client.name,"left: %s;"%reason,len(self.clients),"players"
        if hasattr(client,"ws_connection") and client.ws_connection:
            client.close()
    def send_cmd(self,cmd):
        cmd["time"] = math.floor(self.now()*ticks_per_sec+1/ticks_per_sec)/ticks_per_sec*1000
        cmd = json.dumps({"cmd":cmd})
        for client in self.clients:
            client.write_message(cmd)
    def chat(self,client,lines):
        messages = []
        for line in lines:
            assert isinstance(line,basestring)
            messages.append({client.name: line})
        messages = json.dumps({"chat":messages})
        for recipient in self.clients:
            recipient.write_message(messages)
    def run(self):
        # time out old clients
        stale = time.time() - 3 # 3 secs
        for client in self.clients.copy():
            if client.lastMessage < stale:
                print "timing out",client.name,client.lastMessage-time.time()
                self.remove_client(client,"ping timeout")
        # move simulation onwards?
        while self.tick+self.tick_length <= self.now():
            updates, deaths = [], set()
            for client in self.clients:
                # roll
                if 37 in client.keys: client.roll_speed += roll_speed
                if 39 in client.keys: client.roll_speed -= roll_speed
                client.roll_speed = max(-max_roll_speed,min(max_roll_speed,client.roll_speed))
                if 37 not in client.keys and 39 not in client.keys:
                    client.roll_speed *= .9
                if math.fabs(client.roll_speed) < epslion: client.roll_speed = 0
                # pitch
                if 38 in client.keys: client.pitch_speed -= pitch_speed
                if 40 in client.keys: client.pitch_speed += pitch_speed
                client.pitch_speed = max(-max_pitch_speed,min(max_pitch_speed,client.pitch_speed))
                if 38 not in client.keys and 40 not in client.keys:
                    client.pitch_speed *= .9
                if math.fabs(client.pitch_speed) < epslion: client.pitch_speed = 0
                # yaw
                if 65 in client.keys: client.yaw_speed += yaw_speed
                if 68 in client.keys: client.yaw_speed -= yaw_speed
                client.yaw_speed = max(-max_yaw_speed,min(max_yaw_speed,client.yaw_speed))
                if 65 not in client.keys and 68 not in client.keys:
                    client.yaw_speed *= .9
                if math.fabs(client.yaw_speed) < epslion: client.yaw_speed = 0
                # apply
                client.rot *= euclid.Quaternion().rotate_euler(client.yaw_speed,client.roll_speed,client.pitch_speed)
                client.rot.normalize()
                # speed
                if 83 in client.keys: client.speed -= speed
                if 87 in client.keys: client.speed += speed
                client.speed = max(0.,min(max_speed,client.speed)) # no going backwards
                if 83 not in client.keys and 87 not in client.keys:
                    client.speed *= .9
                if math.fabs(client.speed) < epslion: client.speed = 0            
                move = ((client.rot * forward) * client.rot.conjugated())
                move = euclid.Vector3(move.x,move.y,move.z).normalized() * client.speed
                client.pos += move
                # are we out-of-bounds?
                ### ideally bounce etc, but for now we'll just slide along it
                extreme = .85
                if client.pos.x < -extreme: client.pos.x = -extreme
                if client.pos.x >  extreme: client.pos.x =  extreme
                if client.pos.y < -extreme: client.pos.y = -extreme
                if client.pos.y >  extreme: client.pos.y =  extreme
                if client.pos.z < -extreme: client.pos.z = -extreme
                if client.pos.z >  extreme: client.pos.z =  extreme
                # shooting
                if 32 in client.keys:
                    client.firing += 1
                    if client.firing < 5:
                        self.shots.append(Shot(client))
                else:
                    client.firing = 0
                #print client.name, client.keys, client.roll_speed, client.pitch_speed, client.speed, client.rot, client.pos, move
            for shot in self.shots[:]:
                hit,distance = shot.tick(self.clients)
                if hit:
                    deaths.add(hit)
                    hit.killed_by = shot.client.name
                if hit or not shot.age:
                    self.shots.remove(shot)
            for client in self.clients:
                updates.append({
                    "name":client.name,
                    "pos":(client.pos.x,client.pos.y,client.pos.z),
                    "rot":(client.rot.x,client.rot.y,client.rot.z,client.rot.w),
                    "speed":client.speed,
                })
            for client in self.clients:
                client.write_message(json.dumps({
                        "tick":self.tick,
                        "updates":updates,
                        "shots":[{
                                "pos":(shot.pos.x,shot.pos.y,shot.pos.z),
                                "vec":(shot.vec.x,shot.vec.y,shot.vec.z),
                        } for shot in self.shots],
                }))
            for dead in deaths:
                self.remove_client(dead,"died, shot by %s"%dead.killed_by)
            self.tick += self.tick_length

class LD24WebSocket(tornado.websocket.WebSocketHandler):
    game = Game() # everyone in the same game for now
    def allow_draft76():
    	    print "draft76 rejected"
    	    return False
    def open(self):
        self.closed = False
        self.origin = self.request.headers.get("origin","")
        self.userAgent = self.request.headers.get("user-agent")
        print "connection",self.request.remote_ip, self.origin, self.userAgent
        if self.origin != options.origin and not \
            self.origin.startswith("http://31.192.226.244:") and not \
            self.origin.startswith("http://localhost:"):
            print "kicking out bad origin"
            self.write_message('{"chat":[{"Will":"if you fork the code, you need to run your own server!"}]}')
            self.close()
        chat = [dict([["%d fps"%options.fps,"(lock step frame-rate with all players)"]])]
        if options.welcome:
            chat.append({"welcome":options.welcome})
        self.write_message(json.dumps({"chat":chat}))
        self.lastMessage = time.time()
        self.keys = set()
        self.pos = euclid.Vector3(random.uniform(-.5,.5),random.uniform(-.5,.5),random.uniform(-.5,.5))
        self.rot = euclid.Quaternion().rotate_euler(random.uniform(-.5,.5),random.uniform(-.5,.5),random.uniform(-.5,.5)).normalized()
        self.speed = random.random() * max_speed
        self.roll_speed = self.pitch_speed = self.yaw_speed = 0
        self.firing = 0
        self.killed_by = None
        self.game.add_client(self)
        print self.name,"joined;",len(self.game.clients),"players"
    def on_message(self,message):
        self.lastMessage = time.time()
        try:
            message = json.loads(message)
            assert isinstance(message,dict)
            if "ping" in message:
                assert isinstance(message["ping"],int)
                self.write_message('{"pong":%d}'%message["ping"])
            if "chat" in message:
                assert isinstance(message["chat"],list)
                for line in message["chat"]:
                    assert isinstance(line,basestring)
                    self.game.chat(self,line)
            if "key" in message:
                assert isinstance(message["key"],dict)
                assert isinstance(message["key"]["type"],basestring)
                assert message["key"]["type"] in ("keydown","keyup")
                assert isinstance(message["key"]["value"],int)
                assert message["key"]["value"] in (32,37,38,39,40,65,68,83,87)
                if message["key"]["type"] == "keydown":
                    self.keys.add(message["key"]["value"])
                else:
                    self.keys.discard(message["key"]["value"])
        except:
            print "ERROR processing",message
            traceback.print_exc()
            self.close()
    def write_message(self,msg):
        if self.closed: return
        try:
            tornado.websocket.WebSocketHandler.write_message(self,msg)
        except Exception as e:
            print "ERROR sending join to",self.name,e
            self.closed = True
            self.close()
    def on_close(self):
        if self.closed: return
        self.closed = True
        def do_close():
            self.game.remove_client(self,"went away")
        io_loop.add_callback(do_close)


class MainHandler(tornado.web.RequestHandler):
    def get(self,path):
        # check user access
        auth_header = self.request.headers.get('Authorization') or ""
        authenticated = not len(options.access)
        if not authenticated and auth_header.startswith('Basic '):
            authenticated = base64.decodestring(auth_header[6:]) in options.access
        if not authenticated:
            self.set_status(401)
            self.set_header('WWW-Authenticate', 'Basic realm=Restricted')
            self._transforms = []
            self.finish()
            return
        # check not escaping chroot
        if os.path.commonprefix([os.path.abspath(path),os.getcwd()]) != os.getcwd():
            raise tornado.web.HTTPError(418)
        # get the file to serve
        body = None
        if options.local:
        	try:
        		with open(path,"r") as f:
        			body = f.read()
		except IOError:
			pass
	if not body:
		try:
		    body = subprocess.check_output(["git","show","%s:%s"%(options.branch,path)])
		except subprocess.CalledProcessError:
		    raise tornado.web.HTTPError(404)
        # and set its content-type
        self.set_header("Content-Type",subprocess.Popen(["file","-i","-b","-"],stdout=subprocess.PIPE,
            stdin=subprocess.PIPE, stderr=subprocess.STDOUT).communicate(input=body)[0].split(";")[0])
        # serve it
        self.write(body)
        

application = tornado.web.Application([
    (r"/ws-ld24", LD24WebSocket),
    (r"/(.*)", MainHandler),
])

if __name__ == "__main__":
    parse_command_line()
    set_fps(options.fps)
    application.listen(options.port)
    try:
        io_loop = tornado.ioloop.IOLoop.instance()
        io_loop.start()
    except KeyboardInterrupt:
        print "bye!"
