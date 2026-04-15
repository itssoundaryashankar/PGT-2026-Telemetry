# Sending data from device to device 


## Running Wifi 
1. Run Reciever 
``` python3 reciever.py```
2. Run Sender
``` python3 sender.py --ip <<RECIEVER IP>> --hz 10 --text "hello world" ```

if running in the same machine use RECIEVER IP as 127.0.0.1


## Running LORA
1. To find your device on you computer, use the command:
```ls /dev/tty.*```

