"""
crawler.py

Copyright 2018 Andres Riancho

This file is part of w3af, http://w3af.org/ .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
import time

import w3af.core.controllers.output_manager as om

from w3af.core.controllers.chrome.pool import ChromePool, ChromePoolException
from w3af.core.controllers.chrome.crawler.dom_dump import ChromeCrawlerDOMDump
from w3af.core.controllers.chrome.crawler.js import ChromeCrawlerJS
from w3af.core.controllers.chrome.crawler.exceptions import ChromeCrawlerException
from w3af.core.controllers.chrome.crawler.queue import CrawlerHTTPTrafficQueue
from w3af.core.controllers.chrome.utils.took_line import TookLine
from w3af.core.controllers.chrome.devtools.exceptions import (ChromeInterfaceException,
                                                              ChromeInterfaceTimeout)

from w3af.core.data.fuzzer.utils import rand_alnum


class ChromeCrawler(object):
    """
    Use Google Chrome to crawl a site.

    The basic steps are:
        * Get an InstrumentedChrome instance from the chrome pool
        * Load a URL
        * Receive the HTTP requests generated during loading
        * Send the HTTP requests to the caller
    """

    def __init__(self, uri_opener, max_instances=None, web_spider=None):
        """

        :param uri_opener: The uri opener required by the InstrumentedChrome
                           instances to open URLs via the HTTP proxy daemon.

        :param max_instances: Max number of Chrome processes to spawn. Use None
                              to let the pool decide what is the max.

        :param web_spider: A web_spider instance which is used (if provided) to
                           parse the DOM rendered by Chrome instances.
        """
        self._uri_opener = uri_opener
        self._web_spider = web_spider

        self._pool = ChromePool(self._uri_opener, max_instances=max_instances)

        self._crawl_strategies = [ChromeCrawlerJS(self._pool)]

        if web_spider is not None:
            self._crawl_strategies.append(ChromeCrawlerDOMDump(self._pool, web_spider))

    def crawl(self, url, http_traffic_queue):
        """
        :param url: The URL to crawl

        :param http_traffic_queue: Queue.Queue() where HTTP requests and responses
                                   generated by the browser are sent

        :return: True if the crawling process completed successfully, otherwise
                 exceptions are raised.
        """
        debugging_id = rand_alnum(8)

        self._crawl(url,
                    http_traffic_queue,
                    debugging_id=debugging_id)

    def _cleanup(self,
                 url,
                 chrome,
                 debugging_id=None):
        #
        # In order to remove all the DOM from the chrome instance and clear
        # some memory we load the about:blank page
        #
        took_line = TookLine('Spent %.2f seconds cleaning up')

        try:
            chrome.load_about_blank()
        except (ChromeInterfaceException, ChromeInterfaceTimeout) as cie:
            msg = 'Failed to load about:blank page in chrome browser %s: "%s" (did: %s)'
            args = (chrome, cie, debugging_id)
            om.out.debug(msg % args)

            # Since we got an error we remove this chrome instance from the
            # pool it might be in an error state
            self._pool.remove(chrome)

            raise ChromeCrawlerException('Failed to load about:blank in chrome browser')

        # Success! Return the chrome instance to the pool
        self._pool.free(chrome)

        args = (chrome.http_traffic_queue.count, url, chrome, debugging_id)
        msg = 'Extracted %s new HTTP requests from %s using %s (did: %s)'
        om.out.debug(msg % args)

        took_line.send()

        return True

    def _crawl(self, url, http_traffic_queue, debugging_id=None):
        """
        Use all the crawling strategies to extract links from the loaded page.

        :return:
        """
        for crawl_strategy in self._crawl_strategies:
            try:
                chrome = self._initial_page_load(url,
                                                 http_traffic_queue,
                                                 debugging_id=debugging_id)
            except Exception, e:
                msg = ('Failed to perform the initial page load of %s in'
                       ' chrome crawler: "%s" (did: %s)')
                args = (url, e, debugging_id)
                om.out.debug(msg % args)

                # We want to raise exceptions in order for them to reach
                # the framework's exception handler
                raise

            args = (crawl_strategy.get_name(), url, debugging_id)
            msg = 'Spent %%.2f seconds in crawl strategy %s for %s (did: %s)' % args
            took_line = TookLine(msg)

            try:
                crawl_strategy.crawl(chrome,
                                     url,
                                     debugging_id=debugging_id)
            except Exception, e:
                msg = 'Failed to crawl %s using chrome instance %s: "%s" (did: %s)'
                args = (url, chrome, e, debugging_id)
                om.out.debug(msg % args)

                took_line.send()

                self._pool.remove(chrome)

                # We want to raise exceptions in order for them to reach
                # the framework's exception handler
                raise

            try:
                self._cleanup(url,
                              chrome,
                              debugging_id=debugging_id)
            except Exception, e:
                msg = 'Failed to crawl %s using chrome instance %s: "%s" (did: %s)'
                args = (url, chrome, e, debugging_id)
                om.out.debug(msg % args)

                took_line.send()

                # We want to raise exceptions in order for them to reach
                # the framework's exception handler
                raise

            took_line.send()

    def _get_chrome_from_pool(self, url, http_traffic_queue, debugging_id):
        args = (url, debugging_id)
        msg = 'Getting chrome crawler from pool for %s (did: %s)'
        om.out.debug(msg % args)

        took_line = TookLine('Spent %.2f seconds getting a chrome instance')

        crawler_http_traffic_queue = CrawlerHTTPTrafficQueue(http_traffic_queue,
                                                             debugging_id=debugging_id)

        try:
            chrome = self._pool.get(http_traffic_queue=crawler_http_traffic_queue)
        except ChromePoolException as cpe:
            args = (cpe, debugging_id)
            msg = 'Failed to get a chrome instance: "%s" (did: %s)'
            om.out.debug(msg % args)

            raise ChromeCrawlerException('Failed to get a chrome instance: "%s"' % cpe)

        took_line.send()

        return chrome

    def _initial_page_load(self, url, http_traffic_queue, debugging_id=None):
        """
        Get a chrome instance from the pool and load the initial URL

        :return: A chrome instance which has the initial URL loaded and is
                 ready to be used during crawling.
        """
        chrome = self._get_chrome_from_pool(url,
                                            http_traffic_queue,
                                            debugging_id)

        args = (chrome, url, debugging_id)
        om.out.debug('Using %s to load %s (did: %s)' % args)

        chrome.set_debugging_id(debugging_id)
        start = time.time()

        msg = 'Spent %%.2f seconds loading URL %s in chrome' % url
        took_line = TookLine(msg)

        try:
            chrome.load_url(url)
        except (ChromeInterfaceException, ChromeInterfaceTimeout) as cie:
            args = (url, chrome, cie, debugging_id)
            msg = 'Failed to load %s using %s: "%s" (did: %s)'
            om.out.debug(msg % args)

            # Since we got an error we remove this chrome instance from the pool
            # it might be in an error state
            self._pool.remove(chrome)

            args = (url, chrome, cie)
            raise ChromeCrawlerException('Failed to load %s using %s: "%s"' % args)

        try:
            successfully_loaded = chrome.wait_for_load()
        except (ChromeInterfaceException, ChromeInterfaceTimeout) as cie:
            #
            # Note: Even if we get here, the InstrumentedChrome might have sent
            # a few HTTP requests. Those HTTP requests are immediately sent to
            # the output queue.
            #
            args = (url, chrome, cie, debugging_id)
            msg = ('Exception raised while waiting for page load of %s '
                   'using %s: "%s" (did: %s)')
            om.out.debug(msg % args)

            # Since we got an error we remove this chrome instance from the pool
            # it might be in an error state
            self._pool.remove(chrome)

            args = (url, chrome, cie)
            msg = ('Exception raised while waiting for page load of %s '
                   'using %s: "%s"')
            raise ChromeCrawlerException(msg % args)

        if not successfully_loaded:
            #
            # Just log the fact that the page is not done loading yet
            #
            spent = time.time() - start
            msg = ('Chrome did not successfully load %s in %.2f seconds '
                   'but will try to use the loaded DOM anyway (did: %s)')
            args = (url, spent, debugging_id)
            om.out.debug(msg % args)

        took_line.send()

        took_line = TookLine('Spent %.2f seconds in chrome.stop()')

        #
        # Even if the page has successfully loaded (which is a very subjective
        # term) we click on the stop button to prevent any further requests,
        # changes, etc.
        #
        try:
            chrome.stop()
        except (ChromeInterfaceException, ChromeInterfaceTimeout) as cie:
            msg = 'Failed to stop chrome browser %s: "%s" (did: %s)'
            args = (chrome, cie, debugging_id)
            om.out.debug(msg % args)

            # Since we got an error we remove this chrome instance from the
            # pool it might be in an error state
            self._pool.remove(chrome)

            raise ChromeCrawlerException('Failed to stop chrome browser')

        took_line.send()

        return chrome

    def terminate(self):
        self._pool.terminate()
        self._uri_opener = None
        self._web_spider = None

    def print_all_console_messages(self):
        """
        This method will get the first chrome instance from the pool and print
        all the console.log() messages that it has.

        The method should only be used during unittests, when there is only one
        chrome instance in the pool!

        :return: None, output is written to stdout
        """
        msg = 'Chrome pool has %s instances, one is required' % len(self._pool._free)
        assert len(self._pool._free) == 1, msg

        instrumented_chrome = list(self._pool._free)[0]
        for console_message in instrumented_chrome.get_console_messages():
            print(console_message)

    def get_js_errors(self):
        """
        This method will get the first chrome instance from the pool and return
        the captured JS errors.

        The method should only be used during unittests, when there is only one
        chrome instance in the pool!

        :return: A list of JS errors
        """
        msg = 'Chrome pool has %s instances, one is required' % len(self._pool._free)
        assert len(self._pool._free) == 1, msg

        instrumented_chrome = list(self._pool._free)[0]
        return instrumented_chrome.get_js_errors()
