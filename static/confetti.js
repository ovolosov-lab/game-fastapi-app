// canvas-confetti v1.9.3 (Официальная полная несжатая версия для браузера)
(function (global, factory) {
    if (typeof exports === 'object' && typeof module !== 'undefined') {
        module.exports = factory();
    } else if (typeof define === 'function' && define.amd) {
        define(factory);
    } else {
        global.confetti = factory();
    }
}(this, (function () {
    'use strict';

    function getCanvasWindow(canvas) {
        return canvas.ownerDocument.defaultView;
    }

    function getCanvas() {
        var existingCanvas = document.getElementById('confetti-canvas');
        if (existingCanvas) {
            return existingCanvas; // Если есть, возвращаем его
        }

        var canvas = document.createElement('canvas');
        canvas.id = 'confetti-canvas'; 
        canvas.style.position = 'fixed';
        canvas.style.zIndex = '999999';
        canvas.style.top = '0px';
        canvas.style.left = '0px';
        canvas.style.width = '100vw';
        canvas.style.height = '100vh';        
        // canvas.style.width = '100%';
        // canvas.style.height = '100%';
        canvas.style.pointerEvents = 'none';
        document.body.appendChild(canvas);
        return canvas;
    }

    function factory(options) {
        var canvas = options.canvas || getCanvas();
        var ctx = canvas.getContext('2d');
        var particles = [];

        function resize() {
            canvas.width = window.innerWidth * (window.devicePixelRatio || 1);
            canvas.height = window.innerHeight * (window.devicePixelRatio || 1);
        }

        window.addEventListener('resize', resize, false);
        resize();

        return function fire(confettiOptions) {
            var opts = confettiOptions || {};
            var count = opts.particleCount || 50;
            var angle = (opts.angle === undefined ? 90 : opts.angle) * Math.PI / 180;
            var spread = (opts.spread === undefined ? 45 : opts.spread) * Math.PI / 180;
            var startVelocity = opts.startVelocity || 55;
            var decay = opts.decay || 0.9;
            var colors = opts.colors || ['#26ccff', '#a25afd', '#ff5e7e', '#88ff5a', '#fcff42', '#ffd066', '#1febff'];
            var ticks = opts.ticks || 200;
            var origin = opts.origin || { x: 0.5, y: 0.5 };
            var gravity = opts.gravity === undefined ? 0.9 : opts.gravity;
            var drift = opts.drift || 0;

            for (var i = 0; i < count; i++) {
                var pAngle = angle - spread / 2 + Math.random() * spread;
                var pVelocity = startVelocity + (Math.random() - 0.5) * (startVelocity * 0.5);
                particles.push({
                    x: canvas.width * origin.x,
                    y: canvas.height * origin.y,
                    w: (Math.random() * 8 + 4) * (window.devicePixelRatio || 1),
                    h: (Math.random() * 8 + 4) * (window.devicePixelRatio || 1),
                    vx: pVelocity * Math.cos(pAngle) + drift * (Math.random() - 0.5),
                    vy: -pVelocity * Math.sin(pAngle),
                    color: colors[i % colors.length],
                    opacity: 1,
                    ticks: ticks,
                    tick: 0,
                    decay: decay,
                    gravity: gravity * 0.5
                });
            }

            function update() {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                particles = particles.filter(function (p) {
                    p.x += p.vx;
                    p.y += p.vy;
                    p.vy += p.gravity;
                    p.vx *= p.decay;
                    p.vy *= p.decay;
                    p.opacity = 1 - (p.tick / p.ticks);
                    p.tick++;

                    ctx.save();
                    ctx.globalAlpha = p.opacity;
                    ctx.fillStyle = p.color;
                    ctx.fillRect(p.x, p.y, p.w, p.h);
                    ctx.restore();

                    return p.tick < p.ticks && p.y < canvas.height;
                });

                if (particles.length > 0) {
                    requestAnimationFrame(update);
                //} else if (!options.canvas) {
                //    canvas.remove();
                }
            }
            requestAnimationFrame(update);
        };
    }

    var globalConfetti = factory({ global: true });
    return globalConfetti;
})));
