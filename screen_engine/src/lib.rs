use std::ptr::null_mut;
use std::slice;
use windows_sys::Win32::Foundation::HWND;
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::UI::WindowsAndMessaging::*;

/// Captura la pantalla principal de Windows y la comprime directamente a JPEG.
/// Utiliza formato Top-Down DIB para evitar inversión de líneas y jpeg-encoder
/// con codificación nativa BGRA para evitar permutaciones de bytes en memoria.
#[no_mangle]
pub unsafe extern "C" fn capture_screen(
    quality: u8,
    out_len: *mut usize,
    out_w: *mut u32,
    out_h: *mut u32,
) -> *mut u8 {
    let hdc_screen = GetDC(0 as HWND);
    let width = GetSystemMetrics(SM_CXSCREEN);
    let height = GetSystemMetrics(SM_CYSCREEN);

    if hdc_screen.is_null() || width <= 0 || height <= 0 {
        return null_mut();
    }

    let hdc_mem = CreateCompatibleDC(hdc_screen);
    let hbm_screen = CreateCompatibleBitmap(hdc_screen, width, height);
    let old_obj = SelectObject(hdc_mem, hbm_screen as _);

    // BitBlt desde el contexto de dispositivo de la pantalla hacia la memoria
    BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, 0, 0, SRCCOPY);

    // biHeight negativo = DIB Top-Down
    let mut bmi: BITMAPINFO = std::mem::zeroed();
    bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
    bmi.bmiHeader.biWidth = width;
    bmi.bmiHeader.biHeight = -height;
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32; // 32 bits = BGRA
    bmi.bmiHeader.biCompression = BI_RGB;

    let mut raw_pixels = vec![0u8; (width * height * 4) as usize];
    GetDIBits(
        hdc_mem,
        hbm_screen,
        0,
        height as u32,
        raw_pixels.as_mut_ptr() as _,
        &mut bmi,
        DIB_RGB_COLORS,
    );

    // Limpieza de objetos GDI para prevenir agotamiento de handles del sistema
    SelectObject(hdc_mem, old_obj);
    DeleteObject(hbm_screen as _);
    DeleteDC(hdc_mem);
    ReleaseDC(0 as HWND, hdc_screen);

    // Compresión BGRA a JPEG con soporte SIMD
    let mut jpeg_buf = Vec::with_capacity(raw_pixels.len() / 8);
    let encoder = jpeg_encoder::Encoder::new(&mut jpeg_buf, quality);
    if encoder
        .encode(
            &raw_pixels,
            width as u16,
            height as u16,
            jpeg_encoder::ColorType::Bgra,
        )
        .is_err()
    {
        return null_mut();
    }

    *out_len = jpeg_buf.len();
    *out_w = width as u32;
    *out_h = height as u32;

    // Entregar propiedad del buffer a Python
    let mut boxed_slice = jpeg_buf.into_boxed_slice();
    let ptr = boxed_slice.as_mut_ptr();
    std::mem::forget(boxed_slice);

    ptr
}

/// Libera la memoria previamente asignada por `capture_screen`.
#[no_mangle]
pub unsafe extern "C" fn free_screen_buffer(ptr: *mut u8, len: usize) {
    if !ptr.is_null() && len > 0 {
        let _ = Box::from_raw(slice::from_raw_parts_mut(ptr, len));
    }
}
