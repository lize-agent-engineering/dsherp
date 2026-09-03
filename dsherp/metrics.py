"""In-process Prometheus text 0.0.4 registry and loopback HTTP scrape."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading


def _escape(value):
    return str(value).replace('\\','\\\\').replace('\n','\\n').replace('"','\\"')


def _escape_help(value):
    return str(value).replace('\\','\\\\').replace('\n','\\n')


def _number(value):
    if isinstance(value,bool) or isinstance(value,int):return str(int(value))
    if isinstance(value,float) and value.is_integer():return str(int(value))
    return str(value)


class _Counter:
    def __init__(self,lock,labels):
        self._lock=lock;self._labels=tuple(labels);self._values={}
    def inc(self,amount=1,**labels):
        key=tuple(labels[name] for name in self._labels)
        with self._lock:self._values[key]=self._values.get(key,0)+amount


class _Gauge:
    def __init__(self,lock):
        self._lock=lock;self._value=0
    def set(self,value):
        with self._lock:self._value=value


class _Histogram:
    def __init__(self,lock,buckets):
        self._lock=lock;self._buckets=tuple(buckets)
        self._counts=[0]*(len(self._buckets)+1);self._sum=0;self._count=0
    def observe(self,value):
        with self._lock:
            self._sum+=value;self._count+=1
            for index,bound in enumerate(self._buckets):
                if value<=bound:self._counts[index]+=1
            self._counts[-1]+=1


class Registry:
    def __init__(self):
        self._lock=threading.Lock();self._metrics=[]
    def counter(self,name,help,labels=()):
        metric=_Counter(self._lock,labels);self._metrics.append((name,help,'counter',metric));return metric
    def gauge(self,name,help):
        metric=_Gauge(self._lock);self._metrics.append((name,help,'gauge',metric));return metric
    def histogram(self,name,help,buckets):
        metric=_Histogram(self._lock,buckets);self._metrics.append((name,help,'histogram',metric));return metric
    def render(self):
        lines=[]
        with self._lock:
            for name,help,kind,metric in self._metrics:
                lines.append(f'# HELP {name} {_escape_help(help)}')
                lines.append(f'# TYPE {name} {kind}')
                if kind=='counter':
                    if metric._labels:
                        for key,value in metric._values.items():
                            labels=','.join(f'{label}="{_escape(item)}"' for label,item in zip(metric._labels,key))
                            lines.append(f'{name}{{{labels}}} {_number(value)}')
                    else:
                        lines.append(f'{name} {_number(metric._values.get((),0))}')
                elif kind=='gauge':
                    lines.append(f'{name} {_number(metric._value)}')
                else:
                    for bound,count in zip(metric._buckets,metric._counts):
                        lines.append(f'{name}_bucket{{le="{_number(bound)}"}} {_number(count)}')
                    lines.append(f'{name}_bucket{{le="+Inf"}} {_number(metric._counts[-1])}')
                    lines.append(f'{name}_sum {_number(metric._sum)}')
                    lines.append(f'{name}_count {_number(metric._count)}')
        return '\n'.join(lines)+'\n'


def serve(registry,port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?',1)[0]!='/metrics':
                self.send_error(404);return
            body=registry.render().encode()
            self.send_response(200)
            self.send_header('Content-Type','text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self,format,*args):
            return
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server
