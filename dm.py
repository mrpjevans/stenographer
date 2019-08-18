# Demolition Man Morality Violation Machine
# By PJ Evans <pj@mrpjevans.com>
# MIT Licence

from gpiozero import Button
import re
import sys
import os
import subprocess
from google.cloud import speech
from google.cloud.speech import enums
from google.cloud.speech import types
import pyaudio
from six.moves import queue
from thermalprinter import ThermalPrinter
from PIL import Image
import datetime

# Put your morality words in here between the brackets
# separate each word with a |
# All lower case and do not use spaces
REGEXP = r'\b(raspberry)\b'

# Buttons
BLACK_BUTTON_GPIO = 17
RED_BUTTON_GPIO = 27
mode = 0
recording = False

# Audio recording parameters
RATE = 44100
CHUNK = int(RATE / 10)  # 100ms
DEVICE = 1

print('Demolition Man Morality Violation Ticketing Machine')
os.system('espeak starting\ up')

# Printer
printer = ThermalPrinter(port='/dev/serial0')
receipt = Image.open(os.path.join(
    os.path.dirname(__file__),
    'dm.jpg')
)


def shutdown():
    os.system('espeak shutting\ down')
    os.system('sudo shutdown -h now')


def black_button_pressed():
    global mode, recording
    print('Black button pressed')
    recording = False
    if mode == 0:
        print('Switching to stenographer mode')
        os.system('espeak stenographer\ mode')
        mode = 1
    else:
        print('Switching to morality mode')
        os.system('espeak morality\ mode')
        mode = 0


def red_button_pressed():
    global mode, recording
    print('Red button pressed')
    if not recording:
        print('Starting recording')
        os.system('espeak recording')
        recording = True
        stenographer()

black_button = Button(BLACK_BUTTON_GPIO, hold_time=3)
red_button = Button(RED_BUTTON_GPIO)
black_button.when_held = shutdown
black_button.when_released = black_button_pressed
red_button.when_released = red_button_pressed

print('Ready')
os.system('espeak morality\ mode\ ready')


class MicrophoneStream(object):
    """Opens a recording stream as a generator yielding the audio chunks."""
    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk

        # Create a thread-safe buffer of audio data
        self._buff = queue.Queue()
        self.closed = True

    def __enter__(self):
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            format=pyaudio.paInt16,
            # The API currently only supports 1-channel (mono) audio
            # https://goo.gl/z757pE
            input_device_index=DEVICE,
            channels=1, rate=self._rate,
            input=True, frames_per_buffer=self._chunk,
            # Run the audio stream asynchronously to fill the buffer object.
            # This is necessary so that the input device's buffer doesn't
            # overflow while the calling thread makes network requests, etc.
            stream_callback=self._fill_buffer,
        )

        self.closed = False

        return self

    def __exit__(self, type, value, traceback):
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self._buff.put(None)
        self._audio_interface.terminate()

    def _fill_buffer(self, in_data, frame_count, time_info, status_flags):
        """Continuously collect data from the audio stream, into the buffer."""
        self._buff.put(in_data)
        return None, pyaudio.paContinue

    def generator(self):
        global red_button, recording
        while not self.closed:
            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            while True:
                try:
                    chunk = self._buff.get(False)
                    if chunk is None:
                        return
                    if red_button.is_pressed:
                        print('Exiting recording...')
                        recording = False
                        os.system('espeak recording\ stopped')
                        return
                    data = [chunk]
                    break
                except:
                    pass

            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self._buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            yield b''.join(data)


def listen_print_loop(responses):
    global red_button, mode, REGEXP, receipt
    """Iterates through server responses and prints them.

    The responses passed is a generator that will block until a response
    is provided by the server.

    Each response may contain multiple results, and each result may contain
    multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
    print only the transcription for the top alternative of the top result.

    In this case, responses are provided for interim results as well. If the
    response is an interim one, print a line feed at the end of it, to allow
    the next result to overwrite it, until the response is a final one. For the
    final one, print a newline to preserve the finalized transcription.
    """
    num_chars_printed = 0
    for response in responses:

        if red_button.is_pressed:
            print('Exiting...')
            break

        if not response.results:
            continue

        # The `results` list is consecutive. For streaming, we only care about
        # the first result being considered, since once it's `is_final`, it
        # moves on to considering the next utterance.
        result = response.results[0]
        if not result.alternatives:
            continue

        # Display the transcription of the top alternative.
        transcript = result.alternatives[0].transcript

        # Display interim results, but with a carriage return at the end of the
        # line, so subsequent lines will overwrite them.
        #
        # If the previous result was longer than this one, we need to print
        # some extra spaces to overwrite the previous result
        overwrite_chars = ' ' * (num_chars_printed - len(transcript))

        if not result.is_final:
            sys.stdout.write(transcript + overwrite_chars + '\r')
            sys.stdout.flush()

            num_chars_printed = len(transcript)

        else:

            if mode == 1:
                print(transcript + overwrite_chars)
                printer.out(transcript + overwrite_chars)
                printer.feed(2)

            if mode == 0 and re.search(REGEXP, transcript, re.I):
                os.system('aplay /home/pi/stenographer/dm.wav')
                receipt()

            num_chars_printed = 0


def stenographer():
    # See http://g.co/cloud/speech/docs/languages
    # for a list of supported languages.
    language_code = 'en-GB'  # a BCP-47 language tag

    client = speech.SpeechClient()
    config = types.RecognitionConfig(
        encoding=enums.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code=language_code)
    streaming_config = types.StreamingRecognitionConfig(
        config=config,
        interim_results=True)

    with MicrophoneStream(RATE, CHUNK) as stream:
        audio_generator = stream.generator()
        requests = (types.StreamingRecognizeRequest(audio_content=content)
                    for content in audio_generator)

        responses = client.streaming_recognize(streaming_config, requests)

        # Now, put the transcription responses to use.
        listen_print_loop(responses)

def receipt():
    global printer
    my_date = datetime.datetime.now()
    my_date_string = my_date.strftime('%x')
    my_time_string = my_date.strftime('%X')
    printer = ThermalPrinter(port='/dev/serial0')
    printer.out('MORALITY', double_width=True)
    printer.out('')
    printer.out('TIME: ' + my_time_string)
    printer.out('DATE: ' + my_date_string)
    printer.out('NAME: Spartan John')
    printer.out('VIOLATION: Sotto voce verbal')
    printer.out('PUNISHMENT: Warning & fine')
    printer.out('FINE: 1.5 Credits')
    printer.out('')
    printer.out('VIOLATION', double_width=True)
    printer.feed(3)

while True:
    pass
