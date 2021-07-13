# roblox-ip-spoof
Proxies your requests through roblox.qq.com proxy servers, thus allowing the use of the `Roblox-CNP-True-IP` IP forwarding header.
 
```python
from roblox import Roblox

rbx = Roblox()
resp = rbx.request("GET", "https://www.roblox.com/timg/rbx", headers={"Roblox-CNP-True-IP": "1.1.1.1"})
print(resp.headers)
```
