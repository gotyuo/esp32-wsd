/*
 * 立创开发板软硬件资料与相关扩展板软硬件资料官网全部开源
 * 开发板官网：www.lckfb.com
 * 技术支持常驻论坛，任何技术问题欢迎随时交流学习
 * 立创论坛：https://oshwhub.com/forum
 * 关注bilibili账号：【立创开发板】，掌握我们的最新动态！
 * 不靠卖板赚钱，以培养中国工程师为己任
 * Change Logs:
 * Date           Author       Notes
 * 2024-06-17     LCKFB-LP    first version
 */
#include "board.h"
#include "stdio.h"
#include "bsp_uart.h"
#include "lcd_init.h"
#include "lcd.h"
#include "pic.h"

int32_t main(void)
{
	board_init();	// 开发板初始化
	
	uart1_init(115200);	// 串口1波特率115200
	
    float t = 0;
    
    LCD_Init();//屏幕初始化
    LCD_Fill(0,0,LCD_W,LCD_H,BLACK);//清全屏为黑色
    
    while(1)
    {            
        LCD_ShowString(0,16*2,(uint8_t *)"LCD_W:",WHITE,BLACK,16,0);
        LCD_ShowIntNum(48,16*2,LCD_W,3,WHITE,BLACK,16);
        LCD_ShowString(80,16*2,(uint8_t *)"LCD_H:",WHITE,BLACK,16,0);
        LCD_ShowIntNum(128,16*2,LCD_H,3,WHITE,BLACK,16);
        
        LCD_ShowString(0,16*3,(uint8_t *)"Nun:",WHITE,BLACK,16,0);
        LCD_ShowFloatNum1(8*4,16*3,t,4,WHITE,BLACK,16);
        t+=0.11;
        
        delay_ms(1000);        
    }
}

