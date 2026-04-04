require('dotenv').config();
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode  = require('qrcode-terminal');
const axios   = require('axios');
const http    = require('http'); // built-in — no install needed

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

const TARGET_GROUP_ID = process.env.TARGET_GROUP_ID;
let clientReady = false;

// ── WHATSAPP EVENTS ──────────────────────────────────────────────────────────
client.on('qr', (qr) => {
    qrcode.generate(qr, { small: true });
    console.log('Scan the QR code above to sync WhatsApp.');
});

client.on('ready', () => {
    console.log("✅ ZARA'S BOT IS ACTIVE IN THE GROUP!");
    clientReady = true;
});

client.on('message_create', async (msg) => {
    // 1. SCOPE CHECK — only process messages from the target group sent by the account owner
    const isOurGroup = (msg.from === TARGET_GROUP_ID || msg.to === TARGET_GROUP_ID);
    if (!isOurGroup || !msg.fromMe) return;

    // 2. LOOP PROTECTION — ignore bot-generated replies
    const botMarkers = [
        "📊", "NUTRITION REPORT", "📝", "YOUR RECENT LOGS",
        "🗑️", "🔥", "📜", "❌", "↩️",
        "HYDRATION REMINDER", "GOOD MORNING, ZARA",
        "WATER LOGGED", "WATER STATUS", "GOAL COMPLETE"
    ];
    if (botMarkers.some(m => msg.body.includes(m))) return;

    try {
        let payload = {
            Body:      msg.body || "",
            From:      msg.from,
            ImageData: null
        };

        // 3. MEDIA HANDLING
        if (msg.hasMedia) {
            const media = await msg.downloadMedia();
            payload.ImageData = media.data;
            console.log(`📸 Image downloaded. Data length: ${media.data.length} characters.`);
        }

        // 4. SEND TO PYTHON
        const response = await axios.post('http://127.0.0.1:8000/webhook', payload, {
            headers: { 'Content-Type': 'application/json' }
        });

        if (response.data) {
            await client.sendMessage(TARGET_GROUP_ID, response.data);
            console.log("✅ Reply sent back to group!");
        }

    } catch (error) {
        console.error('❌ Bridge Error:', error.message);
    }
});

// ── OUTBOUND SERVER (port 3001) ───────────────────────────────────────────────
// Python's APScheduler POSTs here to push proactive reminders to WhatsApp.
const outboundServer = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/send') {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', async () => {
            try {
                const { message } = JSON.parse(body);
                if (!clientReady) {
                    res.writeHead(503, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ error: 'WhatsApp client not ready yet' }));
                    return;
                }
                await client.sendMessage(TARGET_GROUP_ID, message);
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ success: true }));
                console.log("💧 Proactive reminder delivered to group.");
            } catch (e) {
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ error: e.message }));
            }
        });
    } else {
        res.writeHead(404);
        res.end();
    }
});

outboundServer.listen(3001, () => {
    console.log('📡 Outbound message server listening on port 3001.');
});

client.initialize();