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
  Install with pip install piper-tts
* Download a voice
  https://huggingface.co/rhasspy/piper-voices/tree/main
  You can place the file in ~/.local/share/piper/
  

## Usage:
* If you copied _weespeak.py_ to _~/.weechat/python/_, you load it by running:

 ```/python load weespeak.py```
 * Then set the backend to piper
  ```/set plugins.var.python.weespeak.backend piper ```
* Then set the voice you want to use
```/set plugins.var.python.weespeak.piper_model ~/.local/share/piper/en_US-amy-medium.onnx```

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
