#!/usr/bin/env python3

from prometheus_client import start_http_server, Summary, REGISTRY
from prometheus_client.core import GaugeMetricFamily
import time
import timeit
import subprocess
import tempfile
import os
import datetime
from xml.etree import ElementTree
import logging


REQUEST_TIME = Summary(
    'request_processing_seconds',
    'Time spent processing request')
SLEEP = int(os.environ.get("NMAP_COLLECTOR_INTERVAL", 30))
PORT = int(os.environ.get("NMAP_COLLECTOR_PORT", 8000))
IP_RANGE = os.environ.get("NMAP_COLLECTOR_IP_RANGE", '192.168.0.0/24')
GROUP_NAME = os.environ.get("NMAP_COLLECTOR_GROUP_NAME", "")
SCAN_METHOD = os.environ.get("NMAP_COLLECTOR_SCAN_METHOD", "-F")

VERBOSE = bool(os.environ.get("NMAP_COLLECTOR_VERBOSE", False))

logging.basicConfig(level=logging.DEBUG if VERBOSE else logging.INFO)

UNIX_EPOCH = datetime.datetime(1970, 1, 1)


class NmapMetrics(object):

    def __init__(self, polling_interval=60):
        self.polling_interval = polling_interval
        self.reset_metrics()

    def reset_metrics(self):
        self.ping = GaugeMetricFamily(
            'nmap_ping_srtt_ms',
            'Ping times of all network devices (devices are labels)',
            labels=["hostname", "ip_address", "group"]
        )
        self.state = GaugeMetricFamily(
            'nmap_port_state',
            'Discovered port state of network devices (devices are labels)',
            labels=[
                "hostname",
                "ip_address",
                "group",
                "proto",
                "portid",
                "service",
                "status",
            ]
        )
        self.tls = GaugeMetricFamily(
            'nmap_tls_expiry',
            'Epoch time of tls enabled service',
            labels=[
                "hostname",
                "ip_address",
                "group",
                "proto",
                "portid",
                "service",
                "epochTime",
            ]
        )

    def run_metrics_loop(self):
        self.reset_metrics()
        while True:
            self.fetch()
            time.sleep(self.polling_interval)

    def collect(self):
        yield self.ping
        yield self.state
        yield self.tls

    @REQUEST_TIME.time()
    def fetch(self):

        start_time = timeit.default_timer()
        logging.debug(f"scanning group {GROUP_NAME}: {IP_RANGE}")

        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.join(tmpdir, 'nmap.xml')
            cmd = ["nmap", "-oX", filename, "-d3"]
            cmd += SCAN_METHOD.split()
            cmd += IP_RANGE.split()
            logging.debug(f"Executing {' '.join(cmd)}")
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            if VERBOSE:
                for out_item in str(p.stdout).split('\\n'):
                    logging.debug(f"out> {out_item}")
                for err_item in str(p.stderr).split('\\n'):
                    logging.debug(f"err> {err_item}")

            done_scanning_time = timeit.default_timer()
            scan_duration = done_scanning_time - start_time

            # purge items
            self.reset_metrics()

            # construct metrics
            self.parse(filename)

            end_time = timeit.default_timer()
            processing_duration = end_time - done_scanning_time

            total_duration = end_time - start_time

            logging.info(f"cycle completed in {total_duration:.2f}s "
                         f"({scan_duration:.2f}s + "
                         f"{processing_duration:.2f}s)")

    def parse(self, filepath):

        assert os.path.isfile(filepath)

        if VERBOSE:
            with open(filepath) as f:
                for line in f.readlines():
                    logging.debug(f"xml> {line.rstrip()}")

        root = ElementTree.parse(filepath).getroot()
        for n in root.findall("host"):
            address = n.find("address").attrib["addr"]
            hostname = None
            try:
                hostnames_el = n.find("hostnames").find("hostname")
                if hostnames_el is not None:
                    hostname = hostnames_el.attrib["name"]
                else:
                    hostname = address
            except Exception as e:
                hostname = address
                logging.error(f"Looking for host, got e: {e}")
            logging.debug(f"NODE {address} ({hostname})")

            # parse ping
            ping_time = None
            try:
                ping_time = int(n.find("times").attrib["srtt"]) / 1000
            except Exception as e:
                ping_time = 0
                logging.error(f"Looking for ping time, got e: {e}")
            logging.debug(f"PING {ping_time}")
            self.ping.add_metric([hostname, address, GROUP_NAME], ping_time)

            ports = n.find("ports")
            if ports:
                for port in ports:
                    logging.debug(f"found port {port}")
                    try:
                        proto = port.attrib["protocol"]
                        portid = port.attrib["portid"]
                        status = port.find("state").attrib["state"]
                        service = 'unknown'
                        try:
                            service = port.find("service").attrib["name"]
                        except Exception:
                            pass
                        stat = 0
                        if status == 'open':
                            stat = 1
                        elif status == 'filtered':
                            stat = -2
                        elif status == 'unfiltered':
                            stat = -1
                        logging.debug(f" PORT proto: {proto} "
                                      f"portid: {portid} service: {service} "
                                      f"status: {status} / {stat}")
                        stat_metric_labels = [
                            hostname,
                            address,
                            GROUP_NAME,
                            proto,
                            portid,
                            service,
                            status,
                        ]
                        self.state.add_metric(stat_metric_labels, stat)

                        # ssl expiry
                        exp = port.find(
                            './/table[@key="validity"]/elem[@key="notAfter"]')
                        logging.debug(f"TLS {exp}")
                        if hasattr(exp, 'text'):
                            dt = datetime.datetime.strptime(
                                exp.text,
                                "%Y-%m-%dT%H:%M:%S")
                            epoch = (dt - UNIX_EPOCH).total_seconds()
                            logging.debug(f"TLS {epoch}")
                            epoch_metric_labels = [
                                hostname,
                                address,
                                GROUP_NAME,
                                proto,
                                portid,
                                service,
                                status,
                            ]
                            self.tls.add_metric(epoch_metric_labels, epoch)
                    except Exception as e:
                        logging.debug(f"could not parse: {e}")


def main():
    logging.info("Started server")
    nmap_metrics = NmapMetrics(polling_interval=SLEEP)
    REGISTRY.register(nmap_metrics)
    start_http_server(PORT)
    nmap_metrics.run_metrics_loop()


if __name__ == '__main__':
    main()
