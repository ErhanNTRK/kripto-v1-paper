import json, urllib.request, time
def kl(iv):
    u = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={iv}&limit=500"
    return json.load(urllib.request.urlopen(u, timeout=10))
now = time.time()*1000
for iv in ("2h","4h"):
    k = kl(iv)
    closed = [x for x in k if x[6] < now]
    cl = [float(x[4]) for x in closed]
    a = 2/51; e = cl[0]
    for c in cl[1:]: e = a*c + (1-a)*e
    last = float(k[-1][4])
    # close needed at next bar so that close > new ema: c > a*c+(1-a)*e -> c > e
    print(iv, "son kapanis", cl[-1], "EMA50", round(e), "| simdi", last, "| sonraki kapanis", time.strftime("%H:%M", time.localtime(k[-1][6]/1000+0.001)), "| gereken >", round(e))
