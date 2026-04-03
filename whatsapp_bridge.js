require('dotenv').config();
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

const client = new Client({
    authStrategy: new LocalAuth(),
    puppeteer: {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    }
});

// Using .env for security
const TARGET_GROUP_ID = process.env.TARGET_GROUP_ID;

client.on('qr', (qr) => {
    qrcode.generate(qr, { small: true });
    console.log('Scan the QR code above to sync WhatsApp.');
});

client.on('ready', () => {
    console.log('✅ ZARA\'S BOT IS ACTIVE IN THE GROUP!');
});

client.on('message_create', async (msg) => {
    // 1. SCOPE CHECK
    const isOurGroup = (msg.from === TARGET_GROUP_ID || msg.to === TARGET_GROUP_ID);
    if (!isOurGroup || !msg.fromMe) return;

    // 2. LOOP PROTECTION
    if (msg.body.includes("📊") || msg.body.includes("NUTRITION REPORT") || msg.body.includes("📝") || msg.body.includes("YOUR RECENT LOGS") || msg.body.includes("🗑️") || msg.body.includes("🔥") || msg.body.includes("📜") || msg.body.includes("❌") || msg.body.includes("↩️")) return;

    try {
        let payload = {
            Body: msg.body || "",
            From: msg.from,
            ImageData: null // Placeholder for image
        };

        // 3. MEDIA HANDLING
        if (msg.hasMedia) {
            const media = await msg.downloadMedia();
            payload.ImageData = media.data;
            console.log(`📸 Image downloaded. Data length: ${media.data.length} characters.`);
        }

        // 4. SEND TO PYTHON (Using JSON now)
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

client.initialize();