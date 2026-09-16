//! The HelloC test Plugin in Rust, with no crates, so it tests the build and not a Plugin API crate.
#![allow(non_snake_case)]
// One code path for u8 and u16 characters, and the exports are only ever called by NSIS
#![allow(
    clippy::missing_safety_doc,
    clippy::useless_conversion,
    clippy::unnecessary_cast
)]

use std::ffi::c_void;
use std::ptr;

#[cfg(feature = "ansi")]
type Char = u8;
#[cfg(not(feature = "ansi"))]
type Char = u16;

/// stack_t from pluginapi.h
#[repr(C)]
pub struct Stack {
    next: *mut Stack,
    text: [Char; 1],
}

const GPTR: u32 = 0x0040;

#[link(name = "kernel32")]
unsafe extern "system" {
    fn GlobalAlloc(flags: u32, bytes: usize) -> *mut c_void;
    fn GlobalFree(mem: *mut c_void) -> *mut c_void;
}

unsafe fn pop(stacktop: *mut *mut Stack) -> Vec<Char> {
    let mut out = Vec::new();
    unsafe {
        let node = *stacktop;
        if node.is_null() {
            return out;
        }
        let text = (&raw const (*node).text).cast::<Char>();
        while *text.add(out.len()) != 0 {
            out.push(*text.add(out.len()));
        }
        *stacktop = (*node).next;
        GlobalFree(node.cast());
    }
    out
}

unsafe fn push(stacktop: *mut *mut Stack, string_size: i32, s: &[Char]) {
    let size = string_size.max(1) as usize;
    unsafe {
        let node = GlobalAlloc(GPTR, size_of::<Stack>() + size * size_of::<Char>()).cast::<Stack>();
        if node.is_null() {
            return;
        }
        // GPTR zeroes the memory, so the terminator is already in place
        let len = s.len().min(size - 1);
        ptr::copy_nonoverlapping(s.as_ptr(), (&raw mut (*node).text).cast::<Char>(), len);
        (*node).next = *stacktop;
        *stacktop = node;
    }
}

fn chars(s: &str) -> Vec<Char> {
    s.bytes().map(Char::from).collect()
}

/// Hello::Greet <name> -> "Hello, <name>!"
#[unsafe(no_mangle)]
pub unsafe extern "C" fn Greet(
    _: *mut c_void,
    string_size: i32,
    _: *mut Char,
    stacktop: *mut *mut Stack,
    _: *mut c_void,
) {
    let mut greeting = chars("Hello, ");
    greeting.extend(unsafe { pop(stacktop) });
    greeting.extend(chars("!"));
    unsafe { push(stacktop, string_size, &greeting) };
}

/// Hello::Add <a> <b> -> a+b
#[unsafe(no_mangle)]
pub unsafe extern "C" fn Add(
    _: *mut c_void,
    string_size: i32,
    _: *mut Char,
    stacktop: *mut *mut Stack,
    _: *mut c_void,
) {
    let int = || -> isize {
        let text: String = unsafe { pop(stacktop) }
            .into_iter()
            .map(|c| c as u8 as char)
            .collect();
        text.parse().unwrap_or(0)
    };
    let sum = int() + int();
    unsafe { push(stacktop, string_size, &chars(&sum.to_string())) };
}
