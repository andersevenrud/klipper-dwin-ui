# Runs once at power-up, before USB enumerates.
# Exposes a second CDC port (the data port, "-if02") for the display pipe and
# names the device so the host can match it with udev / by-id.
import supervisor
import usb_cdc

supervisor.set_usb_identification(manufacturer="dwinui", product="DWIN Bridge")
usb_cdc.enable(console=True, data=True)
