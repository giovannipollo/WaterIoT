from audioop import avg
import logging
from signal import raise_signal
from statistics import mean
import cherrypy
import paho.mqtt.client as mqtt
import requests

from common.WIOTRestApp import *
from common.SettingsManager import *
from common.SettingsNode import *
from common.RESTBase import RESTBase
from common.CatalogRequest import *

class WateringControlAPI(RESTBase):

    def __init__(self, upperRESTSrvcApp, settings: SettingsNode) -> None:
        super().__init__(upperRESTSrvcApp, 0)
        self._catreq = CatalogRequest(self.logger, settings)
        self._catreq.subscribeMQTT("RaspberryDevConn", "/+/airhumidity")
        self._catreq.callbackOnTopic("RaspberryDevConn", "/+/airhumidity", self.onAirHumidity)
        self._catreq.subscribeMQTT("RaspberryDevConn", "/+/airtemperature")
        self._catreq.callbackOnTopic("RaspberryDevConn", "/+/airtemperature", self.onAirTemperature)
        self._catreq.subscribeMQTT("RaspberryDevConn", "/+/terrainhumidity")
        self._catreq.callbackOnTopic("RaspberryDevConn", "/+/terrainhumidity", self.onTerrainHumidity)

        self._avgAirHum = -1
        self._avgAirTemp = -1
        self._avgSoilHum = -1

        self._lat = 0
        self._lon = 0

        self._last_sent_msg_timestamp = -1
        r = self._catreq.reqREST("DeviceConfig", "/configs?path=/watering/min_time_between_messages_sec")
        if r.status and r.code_response == 200:
            self._min_time_between_messages = int(r.json_response["v"])
        else:
            raise Exception(f"Error getting the min time between messages from the DeviceConfig ({r.code_response}): {r.json_response}")

        r = self._catreq.reqREST("DeviceConfig", "/configs?path=/watering/thresholds")
        if r.status and r.code_response == 200:
            self._airtemp_threshold_max = r.json_response["temp"]["max"]
            self._airtemp_threshold_min = r.json_response["temp"]["min"]
            self._airhum_threshold_max = r.json_response["airhum"]["max"]
            self._airhum_threshold_min = r.json_response["airhum"]["min"]
            self._soilhum_threshold_max = r.json_response["soilhum"]["max"]
            self._soilhum_threshold_min = r.json_response["soilhum"]["min"]
        else:
            raise Exception(f"Error getting the thresholds from the DeviceConfig ({r.code_response}): {r.json_response}")
        
        r = self._catreq.reqREST("DeviceConfig", "/configs?path=/system/location")
        if r.status and r.code_response == 200:
            self._lat = r.json_response["lat"]
            self._lon = r.json_response["lon"]
        else:
            raise Exception(f"Error getting the location from the DeviceConfig ({r.code_response}): {r.json_response}")

    
    @cherrypy.tools.json_out()
    def GET(self, *path, **args):
        
        if len(path) == 0:
            return self.asjson_info("WateringControl endpoint!")
        else:
            if path[0] == "status":
                return self.asjson({
                    "asdrubale": {
                        "averages": {
                            "air_humidity": self._avgAirHum,
                            "air_temperature": self._avgAirTemp,
                            "soil_humidity": self._avgSoilHum
                        },
                        "thresholds": {
                            "air_temperature": {
                                "max": self._airtemp_threshold_max,
                                "min": self._airtemp_threshold_min
                            },
                            "air_humidity": {
                                "max": self._airhum_threshold_max,
                                "min": self._airhum_threshold_min
                            },
                            "soil_humidity": {
                                "max": self._soilhum_threshold_max,
                                "min": self._soilhum_threshold_min
                            }
                        }
                    },
                    "location": {
                        "lat": self._lat,
                        "lon": self._lon
                    },
                    "telegram": {
                        "last_sent_msg_timestamp": self._last_sent_msg_timestamp,
                        "min_time_between_messages": self._min_time_between_messages
                    },
                })

        return self.asjson_error("Not found", 404)


    def onAirHumidity(self, paho_mqtt, userdata, msg: mqtt.MQTTMessage):
        payl = json.loads(msg.payload.decode("utf-8"))
        self.logger.debug(f"Air humidity: {payl}")

        status, json_response, code_response = self._catreq.reqREST("ThingSpeakAdaptor", "/humidity?results=10")
        if status == True and code_response == 200:
            self.logger.debug(f"ThingSpeakAdaptor response: {json_response}")

            elements = [{"field1": element["field1"], "field2": element["field2"], "field3": element["field3"]} for element in json_response]
            lastTimestamp = float(elements[-1]["field3"])

            # check if the two timestamps are different, that means that the
            # received value has not been pushed to thingspeak yet
            if float(payl["t"]) > lastTimestamp:
                elements = [*elements, {"field1": payl["v"], "field2": payl["i"], "field3": payl["t"]}]

            self._avgAirHum = mean([float(element["field1"]) for element in elements])

        else:
            self.logger.debug("Error making the request to ThingSpeakAdaptor")
        
        self._asdrubale()
    
    def onAirTemperature(self, paho_mqtt, userdata, msg: mqtt.MQTTMessage):
        payl = json.loads(msg.payload.decode("utf-8"))
        self.logger.debug(f"Air temperature: {payl}")

        status, json_response, code_response = self._catreq.reqREST("ThingSpeakAdaptor", "/temperature?results=10")
        if status == True and code_response == 200:
            self.logger.debug(f"ThingSpeakAdaptor response: {json_response}")

            elements = [{"field1": element["field1"], "field2": element["field2"], "field3": element["field3"]} for element in json_response]
            lastTimestamp = float(elements[-1]["field3"])

            # check if the two timestamps are different, that means that the
            # received value has not been pushed to thingspeak yet
            if float(payl["t"]) > lastTimestamp:
                elements = [*elements, {"field1": payl["v"], "field2": payl["i"], "field3": payl["t"]}]

            self._avgAirTemp = mean([float(element["field1"]) for element in elements])
        
        else:
            self.logger.debug("Error making the request to ThingSpeakAdaptor")
        
        self._asdrubale()

    def onTerrainHumidity(self, paho_mqtt, userdata, msg: mqtt.MQTTMessage):
        payl = json.loads(msg.payload.decode("utf-8"))
        self.logger.debug(f"Terrain humidity: {payl}")

        status, json_response, code_response = self._catreq.reqREST("ThingSpeakAdaptor", "/soil?results=10")
        if status == True and code_response == 200:
            self.logger.debug(f"ThingSpeakAdaptor response: {json_response}")

            elements = [{"field1": element["field1"], "field2": element["field2"], "field3": element["field3"]} for element in json_response]
            lastTimestamp = float(elements[-1]["field3"])

            # check if the two timestamps are different, that means that the
            # received value has not been pushed to thingspeak yet
            if float(payl["t"]) > lastTimestamp:
                elements = [*elements, {"field1": payl["v"], "field2": payl["i"], "field3": payl["t"]}]

            self._avgSoilHum = mean([float(element["field1"]) for element in elements])
        
        else:
            self.logger.debug("Error making the request to ThingSpeakAdaptor")
        
        self._asdrubale()

    def _asdrubale(self):

        self.logger.debug(f"Executing the ASDRUBALE algorithm with: avgAirTemp: {self._avgAirTemp}, avgAirHum: {self._avgAirHum}, avgSoilHum: {self._avgSoilHum}")

        try:
            # logic for the watering control
            entered = False
            if self._avgAirTemp == -1 or self._avgAirHum == -1 or self._avgSoilHum == -1:
                return

            if self._avgSoilHum > self._soilhum_threshold_max:
                entered = True
                self.logger.debug("Soil humidity is too high, watering is not needed")
                r = self._catreq.reqREST("ArduinoDevConn", "/switch?state=off")
                if r.status == True and r.code_response == 200:
                    self.logger.debug("Switched off the watering")
                else:
                    raise Exception(f"Error contacting the Arduino Device Connector ({r.code_response}): {r.json_response}")


            if self._avgSoilHum < self._soilhum_threshold_min:
                entered = True
                self.logger.debug("Soil humidity is too low, watering is needed")
                r = self._catreq.reqREST("OpenWeatherAdaptor", f"/currentweather?lat={self._lat}&lon={self._lon}")
                if r.status == True and r.code_response == 200:
                    if r.json_response["weather"][0]["main"] in {"Rain", "Snow", "Thunderstorm", "Drizzle"} and self._avgSoilHum > self._soilhum_threshold_max:
                        self.logger.debug("It is raining, watering is not needed")
                        r = self._catreq.reqREST("ArduinoDevConn", "/switch?state=off")
                        if r.status == True and r.code_response == 200:
                            self.logger.debug("Switched off the watering")
                        else:
                            raise Exception(f"Error contacting the Arduino Device COnnector ({r.code_response}): {r.json_response}")
                    else:
                        r = self._catreq.reqREST("OpenWeatherAdaptor", f"/forecastweather?lat={self._lat}&lon={self._lon}")
                        if r.status == True and r.code_response == 200:
                            if r.json_response["hourly"][5]["weather"][0]["main"] in {"Rain", "Snow", "Thunderstorm", "Drizzle"}:
                                self.logger.debug("It will rain, watering is not needed")
                                r = self._catreq.reqREST("ArduinoDevConn", "/switch?state=off")
                                if r.status == True and r.code_response == 200:
                                    self.logger.debug("Switched off the watering")
                                else:
                                    raise Exception(f"Error while contacting the Arduino Device Connector ({r.code_response}): {r.json_response}")
                            else:
                                if self._avgAirTemp > self._airtemp_threshold_max and self._avgAirHum < self._airhum_threshold_min:
                                    self.logger.debug("It is too hot, watering is needed")
                                    r = self._catreq.reqREST("ArduinoDevConn", "/switch?state=on")
                                    if r.status == True and r.code_response == 200:
                                        self.logger.debug("Switched off the watering")
                                    else:
                                        raise Exception(f"Error contacting the Arduino Device Connector ({r.code_response}): {r.json_response}")
                                else:
                                    if self._last_sent_msg_timestamp == -1 or time.time() - self._last_sent_msg_timestamp > self._min_time_between_messages:
                                        r = self._catreq.reqREST("TelegramAdaptor", "/sendMessage?text=Hey, the situation is critical. Do you want to /switch on the irrigation?")
                                        if r.status == True and r.code_response == 200:
                                            self.logger.debug("Sent the message to Telegram")
                                            self._last_sent_msg_timestamp = time.time()
                                        else:
                                            raise Exception(f"Error contacting the Telegram Adaptor to send a message ({r.code_response}): {r.json_response}")
                        else:
                            raise Exception(f"Error while getting the forecast from the OpenWeatherAdaptor ({r.code_response}): {r.json_response}")
                else:
                    raise Exception(f"Error while getting the forecast from the OpenWeatherAdaptor ({r.code_response}): {r.json_response}")


            
            if not entered:
                if self._avgSoilHum < mean([self._soilhum_threshold_min, self._soilhum_threshold_max]):
                    self.logger.debug("Soil humidity is in the range, ask the user what to do")
                    if self._last_sent_msg_timestamp == -1 or time.time() - self._last_sent_msg_timestamp > self._min_time_between_messages:
                        r = self._catreq.reqREST("TelegramAdaptor", "/sendMessage?text=Hey, the situation is pretty normal. Do you want to /switch on the irrigation?")
                        if r.status == True and r.code_response == 200:
                            self.logger.debug("Sent the message to Telegram")
                            self._last_sent_msg_timestamp = time.time()
                        else:
                            raise Exception(f"Error contacting the Telegram Adaptor to send a message ({r.code_response}): {r.json_response}")
        except Exception as e:
            self.logger.error(f"Error executing the ASDRUBALE algorithm: {str(e)}", exc_info=True)

class App(WIOTRestApp):
    def __init__(self) -> None:

        super().__init__(log_stdout_level=logging.DEBUG)

        try:
            self._settings = SettingsManager.json2obj(SettingsManager.relfile2abs("settings.json"), self.logger)
            self.create(self._settings, "WateringControl", ServiceType.SERVICE)

            self.addRESTEndpoint("/status")

            self.mount(WateringControlAPI(self, self._settings), self.conf)
            self.loop()

        except Exception as e:
            self.logger.exception(str(e))


if __name__ == "__main__":
    App()