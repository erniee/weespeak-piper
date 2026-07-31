# weespeak - piper in weechat
I really liked the idea of the script providing text-to-speech, but I was not a fan of Espeak's voices. 
I modified the script using Claude to use Piper 

weespeak will output the incoming messages on the current channel-buffer through piper.

## Source

* http://github.com/erniee/weespeak-piper

 ```git clone git://github.com/erniee/weespeak-piper.git weespeak```

## Dependencies

* weechat http://weechat.org/
* python-espeak https://launchpad.net/python-espeak
* Piper https://github.com/OHF-Voice/piper1-gpl

## Usage:
* If you copied _weespeak.py_ to _~/.weechat/python/_, you load it by running:

 ```/python load weespeak.py```

* Otherwise load it from any path:

 ```/python load /path/to/script/weespeak.py```

* List muted Nicks:

 ```/weespeak list_muted```

* Mute Nick:

 ```/weespeak mute elvis```

* Nicks are whitespace separated:

 ```/weespeak mute elvis rob```
* The same rules apply to unmute:

 ```/weespeak unmute elvis```

 ```/weespeak unmute elvis rob```

## TODO
## License
