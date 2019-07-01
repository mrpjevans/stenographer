from thermalprinter import ThermalPrinter

with ThermalPrinter(port='/dev/serial0') as printer:
    printer.out("Hello, world!")
    printer.feed(2)
