'use strict';
/* Local TCP ports. Everything listens on 127.0.0.1 only, so no firewall prompt appears. */
const net = require('node:net');

function isFree(port, host = '127.0.0.1') {
  return new Promise(resolve => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen({port, host, exclusive: true}, () => server.close(() => resolve(true)));
  });
}

function freePort(host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen({port: 0, host, exclusive: true}, () => {
      const {port} = server.address();
      server.close(() => resolve(port));
    });
  });
}

/** The preferred port keeps the page's origin (and its saved preferences) stable between runs. */
async function pickPort(preferred) {
  if (preferred && await isFree(preferred)) return {port: preferred, preferred: true};
  return {port: await freePort(), preferred: false};
}

module.exports = {isFree, freePort, pickPort};
