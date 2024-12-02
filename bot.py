import os
import asyncio
import json
import logging
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
import websockets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters, JobQueue
import random

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global variables
TRANSACTIONS_FILE = 'transactions.csv'
WALLETS_FILE = 'wallets.json'

# Initialize wallets file if not exists
if not os.path.exists(WALLETS_FILE):
    with open(WALLETS_FILE, 'w') as f:
        json.dump({}, f)

def load_wallets():
    """Load wallets from file."""
    with open(WALLETS_FILE, 'r') as f:
        return json.load(f)

def save_wallets(wallets):
    """Save wallets to file."""
    with open(WALLETS_FILE, 'w') as f:
        json.dump(wallets, f)

# Initialize DataFrame if not exists
if not os.path.exists(TRANSACTIONS_FILE):
    # 创建一个新的DataFrame，确保所有必需的列都存在
    df = pd.DataFrame(columns=[
        'timestamp',
        'type',
        'amount',
        'purpose',
        'wallet_alias',
        'transaction_id'
    ])
    df.to_csv(TRANSACTIONS_FILE, index=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        '🎉 Welcome to the Crypto Accounting Bot! 🎉\n\n'
        '📝 Available Commands:\n'
        '▫️ /start - Start the bot\n'
        '▫️ /subscribe <alias> <address> - Monitor wallet\n'
        '   Example: /subscribe main AArPXm8J...\n'
        '▫️ /unsubscribe <alias> - Stop monitoring\n'
        '▫️ /list - View all monitored wallets\n'
        '▫️ /summary [alias] - View spending summary\n'
        '▫️ /categories [alias] - View spending by categories\n\n'
        '💡 Tip: Use short, memorable aliases for your wallets!'
    )

async def subscribe_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start monitoring a wallet."""
    chat_id = update.effective_chat.id
    
    if len(context.args) < 2:
        await update.message.reply_text(
            '⚠️ Please provide both alias and wallet address.\n\n'
            '📝 Correct Usage:\n'
            '▫️ /subscribe <alias> <address>\n'
            '📱 Example:\n'
            '▫️ /subscribe main AArPXm8JatJiuyEffuC1un2Sc835SULa4uQqDcaGpAjV'
        )
        return

    alias = context.args[0]
    address = context.args[1]
    
    # Load existing wallets
    wallets = load_wallets()
    
    # Check if alias already exists
    if alias in wallets:
        await update.message.reply_text(
            f'❌ Alias "{alias}" is already in use.\n'
            '💡 Please choose a different alias.'
        )
        return
    
    # Add new wallet
    wallets[alias] = address
    save_wallets(wallets)
    
    # Remove existing jobs if any
    current_jobs = context.job_queue.get_jobs_by_name(f'monitor_wallet_{alias}')
    for job in current_jobs:
        job.schedule_removal()
    
    # Add new monitoring job
    context.job_queue.run_repeating(
        monitor_wallet,
        interval=1,
        first=0,
        name=f'monitor_wallet_{alias}',
        chat_id=chat_id,
        data={'alias': alias, 'address': address}
    )
    
    # 如果这是第一个钱包，启动模拟交易生成器
    mock_jobs = context.job_queue.get_jobs_by_name('mock_transaction_generator')
    if not mock_jobs:
        context.job_queue.run_repeating(
            generate_mock_transaction,
            interval=10,  # 每10秒生成一次
            first=2,  # 2秒后开始第一次生成
            name='mock_transaction_generator',
            chat_id=chat_id
        )
    
    await update.message.reply_text(
        f'✅ Successfully added wallet!\n\n'
        f'🏷️ Alias: {alias}\n'
        f'📝 Address: {address}\n\n'
        f'🔍 Now monitoring transactions...\n'
        f'💡 Mock transactions will be generated every 10 seconds for testing.'
    )

async def unsubscribe_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop monitoring a wallet."""
    if not context.args:
        wallets = load_wallets()
        if not wallets:
            # 如果没有钱包了，停止模拟交易生成器
            mock_jobs = context.job_queue.get_jobs_by_name('mock_transaction_generator')
            for job in mock_jobs:
                job.schedule_removal()
            
            await update.message.reply_text('📭 No wallets are currently being monitored.')
            return
        
        wallet_list = '\n'.join([f'🏷️ "{alias}": {address[:8]}...{address[-6:]}' for alias, address in wallets.items()])
        await update.message.reply_text(
            '⚠️ Please specify which wallet to unsubscribe.\n\n'
            '📝 Usage: /unsubscribe <alias>\n\n'
            '📋 Currently monitored wallets:\n' + wallet_list
        )
        return

    alias = context.args[0]
    wallets = load_wallets()
    
    if alias not in wallets:
        await update.message.reply_text(
            f'❌ Wallet alias "{alias}" not found.\n'
            '💡 Use /list to see all monitored wallets.'
        )
        return
    
    # Remove monitoring job
    current_jobs = context.job_queue.get_jobs_by_name(f'monitor_wallet_{alias}')
    for job in current_jobs:
        job.schedule_removal()
    
    # Remove wallet from storage
    address = wallets.pop(alias)
    save_wallets(wallets)
    
    await update.message.reply_text(
        f'✅ Successfully unsubscribed!\n\n'
        f'🏷️ Alias: {alias}\n'
        f'📝 Address: {address[:8]}...{address[-6:]}'
    )

async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all monitored wallets."""
    wallets = load_wallets()
    if not wallets:
        await update.message.reply_text('📭 No wallets are currently being monitored.')
        return
    
    wallet_list = '\n'.join([
        f'🏷️ {alias}:\n'
        f'📝 {address[:8]}...{address[-6:]}'
        for alias, address in wallets.items()
    ])
    await update.message.reply_text(
        '📋 Monitored Wallets:\n\n' + wallet_list + '\n\n'
        '💡 Use /summary <alias> to view specific wallet statistics'
    )

async def monitor_wallet(context: ContextTypes.DEFAULT_TYPE):
    """Monitor wallet transactions through WebSocket."""
    job_data = context.job.data
    alias = job_data['alias']
    address = job_data['address']
    
    try:
        uri = "wss://pumpportal.fun/api/data"
        async with websockets.connect(uri) as websocket:
            payload = {
                "method": "subscribeAccountTrade",
                "keys": [address]
            }
            await websocket.send(json.dumps(payload))
            
            async for message in websocket:
                try:
                    data = json.loads(message)
                    logger.info(f"Received data for {alias}: {data}")
                    
                    # 生成唯一交易ID
                    tx_id = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                    data['id'] = tx_id
                    
                    # 验证必要的数据字段
                    if 'amount' not in data:
                        logger.warning(f"Received incomplete transaction data for {alias}: {data}")
                        continue
                    
                    # Add wallet alias to transaction data
                    data['wallet_alias'] = alias
                    # Process transaction
                    await process_transaction(data, context)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse message for {alias}: {e}")
                except Exception as e:
                    logger.error(f"Error processing message for {alias}: {e}")
                    continue
    except websockets.exceptions.ConnectionClosed:
        logger.warning(f"WebSocket connection closed for {alias}, attempting to reconnect...")
        await asyncio.sleep(5)  # 等待5秒后重试
    except Exception as e:
        logger.error(f"Error in monitor_wallet for {alias} ({address}): {str(e)}")
        await asyncio.sleep(5)  # 等待5秒后重试

async def process_transaction(data, context):
    """Process incoming transaction and request purpose from user."""
    try:
        # Extract transaction details
        amount = data.get('amount', 0)
        tx_type = 'incoming' if amount > 0 else 'outgoing'
        alias = data.get('wallet_alias', 'unknown')
        tx_id = data.get('id', 'unknown')
        
        logger.info(f"Processing transaction - Amount: {amount}, Type: {tx_type}, Alias: {alias}, ID: {tx_id}")
        
        # 为不同类型的交易使用不同的emoji
        tx_emoji = '📥' if tx_type == 'incoming' else '📤'
        
        keyboard = [
            [
                InlineKeyboardButton("Food 🍔", callback_data=f"purpose_food_{tx_id}_{alias}"),
                InlineKeyboardButton("Transport 🚗", callback_data=f"purpose_transport_{tx_id}_{alias}")
            ],
            [
                InlineKeyboardButton("Shopping 🛍️", callback_data=f"purpose_shopping_{tx_id}_{alias}"),
                InlineKeyboardButton("Other 📝", callback_data=f"purpose_other_{tx_id}_{alias}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = (
            f"{tx_emoji} New Transaction Detected!\n\n"
            f"🏷️ Wallet: {alias}\n"
            f"💰 Amount: {abs(amount)}\n"
            f"🔍 Type: {tx_type}\n"
            f"📝 ID: {tx_id}\n\n"
            f"Please select the purpose:"
        )
        
        logger.info(f"Sending message with text: {message_text}")
        
        # Send notification to user
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=message_text,
            reply_markup=reply_markup
        )
        
        logger.info("Successfully sent transaction notification")
        
    except Exception as e:
        logger.error(f"Error in process_transaction: {str(e)}", exc_info=True)

async def handle_purpose_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the purpose selection for a transaction."""
    try:
        query = update.callback_query
        await query.answer()
        
        logger.info(f"Received callback query data: {query.data}")
        logger.info(f"Message text: {query.message.text}")
        
        # Extract purpose, transaction ID and wallet alias from callback data
        callback_parts = query.data.split('_')
        logger.info(f"Callback parts: {callback_parts}")
        
        # 移除验证长度的检查，因为交易ID可能包含下划线
        if len(callback_parts) < 3:
            raise ValueError(f"Invalid callback data format: {query.data}")
            
        purpose = callback_parts[1]
        # 合并中间部分作为交易ID
        tx_id = '_'.join(callback_parts[2:-1])
        alias = callback_parts[-1]
        
        logger.info(f"Extracted data - Purpose: {purpose}, TX ID: {tx_id}, Alias: {alias}")
        
        # Extract amount from message text
        message_lines = query.message.text.split('\n')
        logger.info(f"Message lines: {message_lines}")
        
        amount_line = next(line for line in message_lines if 'Amount:' in line)
        amount_str = amount_line.split(':')[1].strip()
        amount = float(amount_str)
        
        logger.info(f"Extracted amount: {amount}")
        
        # Determine transaction type from message text
        is_incoming = '📥' in message_lines[0]
        tx_type = 'incoming' if is_incoming else 'outgoing'
        logger.info(f"Transaction type: {tx_type}")
        
        # Create new transaction record
        new_row = pd.DataFrame({
            'timestamp': [datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            'type': [tx_type],
            'amount': [abs(amount)],
            'purpose': [purpose],
            'wallet_alias': [alias],
            'transaction_id': [tx_id]
        })
        
        logger.info(f"Created new row DataFrame: {new_row.to_dict('records')}")
        
        try:
            if os.path.exists(TRANSACTIONS_FILE):
                logger.info("Reading existing transactions file")
                df = pd.read_csv(TRANSACTIONS_FILE)
                logger.info(f"Existing DataFrame columns: {df.columns.tolist()}")
            else:
                logger.info("Transactions file does not exist, creating new DataFrame")
                df = pd.DataFrame(columns=new_row.columns)
        except Exception as e:
            logger.warning(f"Error reading transactions file: {str(e)}. Creating new DataFrame")
            df = pd.DataFrame(columns=new_row.columns)
        
        # Append new transaction
        df = pd.concat([df, new_row], ignore_index=True)
        logger.info(f"Final DataFrame shape: {df.shape}")
        
        # Save to file
        try:
            df.to_csv(TRANSACTIONS_FILE, index=False)
            logger.info(f"Successfully saved to {TRANSACTIONS_FILE}")
        except Exception as e:
            logger.error(f"Error saving to CSV: {str(e)}")
            raise
        
        # Get emoji for purpose
        purpose_emojis = {
            'food': '🍔',
            'transport': '🚗',
            'shopping': '🛍️',
            'other': '📝'
        }
        purpose_emoji = purpose_emojis.get(purpose, '📝')
        
        # Update the message with the selected purpose
        new_text = f"{query.message.text}\n\n✅ Categorized as: {purpose} {purpose_emoji}"
        logger.info(f"Updating message with text: {new_text}")
        
        await query.edit_message_text(text=new_text)
        logger.info("Successfully updated message")
        
    except Exception as e:
        logger.error(f"Error in handle_purpose_selection: {str(e)}", exc_info=True)
        logger.error(f"Query data: {query.data if query else 'No query'}")
        logger.error(f"Message text: {query.message.text if query and query.message else 'No message'}")
        
        await update.effective_message.reply_text(
            "❌ Sorry, there was an error processing your selection.\n"
            "💡 Please try again."
        )

async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show spending summary."""
    df = pd.read_csv(TRANSACTIONS_FILE)
    if len(df) == 0:
        await update.message.reply_text('📭 No transactions recorded yet!')
        return
    
    # Check if specific wallet alias is provided
    if context.args:
        alias = context.args[0]
        df = df[df['wallet_alias'] == alias]
        if len(df) == 0:
            await update.message.reply_text(f'📭 No transactions found for wallet "{alias}"')
            return
        wallet_info = f' for wallet "{alias}"'
    else:
        wallet_info = ' (All Wallets)'
    
    total_in = df[df['type'] == 'incoming']['amount'].sum()
    total_out = df[df['type'] == 'outgoing']['amount'].sum()
    net_balance = total_in - total_out
    
    # 使用箭头emoji表示净余额的趋势
    balance_emoji = '↗️' if net_balance > 0 else '↘️' if net_balance < 0 else '➡️'
    
    summary = (
        f"📊 Financial Summary{wallet_info}\n\n"
        f"📥 Total Income: {total_in:.2f}\n"
        f"📤 Total Spending: {total_out:.2f}\n"
        f"💰 Net Balance: {net_balance:.2f} {balance_emoji}"
    )
    await update.message.reply_text(summary)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show spending by categories."""
    df = pd.read_csv(TRANSACTIONS_FILE)
    if len(df) == 0:
        await update.message.reply_text('📭 No transactions recorded yet!')
        return
    
    # Check if specific wallet alias is provided
    if context.args:
        alias = context.args[0]
        df = df[df['wallet_alias'] == alias]
        if len(df) == 0:
            await update.message.reply_text(f'📭 No transactions found for wallet "{alias}"')
            return
        wallet_info = f' for wallet "{alias}"'
    else:
        wallet_info = ' (All Wallets)'
    
    category_summary = df[df['type'] == 'outgoing'].groupby('purpose')['amount'].sum()
    
    # 为每个类别添加emoji
    category_emojis = {
        'food': '🍔',
        'transport': '🚗',
        'shopping': '🛍️',
        'other': '📝'
    }
    
    summary = f"📊 Spending by Categories{wallet_info}\n\n"
    total_spending = category_summary.sum()
    
    for category, amount in category_summary.items():
        emoji = category_emojis.get(category, '📝')
        percentage = (amount / total_spending * 100) if total_spending > 0 else 0
        summary += f"{emoji} {category}: {amount:.2f} ({percentage:.1f}%)\n"
    
    summary += f"\n💰 Total Spending: {total_spending:.2f}"
    
    await update.message.reply_text(summary)

async def generate_mock_transaction(context: ContextTypes.DEFAULT_TYPE):
    """Generate a mock transaction for testing purposes."""
    try:
        # 获取所有已监控的钱包
        wallets = load_wallets()
        if not wallets:
            return
        
        # 随机选择一个钱包
        alias = random.choice(list(wallets.keys()))
        address = wallets[alias]
        
        # 生成随机金额 (0.1 到 10.0)
        amount = round(random.uniform(0.1, 10.0), 2)
        # 随机决定是收入还是支出 (20% 概率是收入)
        if random.random() < 0.2:
            amount = abs(amount)  # 收入为正数
        else:
            amount = -abs(amount)  # 支出为负数
            
        # 创建模拟交易数据
        mock_data = {
            'amount': amount,
            'wallet_alias': alias,
            'id': f"mock_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            'address': address
        }
        
        # 处理交易
        await process_transaction(mock_data, context)
        
    except Exception as e:
        logger.error(f"Error generating mock transaction: {str(e)}")

def main():
    """Start the bot."""
    # Create the Application with persistence and job queue
    application = (
        Application.builder()
        .token(os.getenv('TELEGRAM_TOKEN'))
        .job_queue(JobQueue())
        .build()
    )

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe_wallet))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_wallet))
    application.add_handler(CommandHandler("list", list_wallets))
    application.add_handler(CommandHandler("summary", show_summary))
    application.add_handler(CommandHandler("categories", show_categories))
    application.add_handler(CallbackQueryHandler(handle_purpose_selection))

    # Run the bot
    application.run_polling()

if __name__ == '__main__':
    main()
