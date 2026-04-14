/**
 * proxy_inject.m
 *
 * DYLD-injectable library that swizzles NSURLSessionConfiguration
 * to force all NSURLSession traffic through an HTTP proxy.
 *
 * Usage:
 *   SIMCTL_CHILD_DYLD_INSERT_LIBRARIES=/path/to/libproxy_inject.dylib
 *   SIMCTL_CHILD_PROXY_HOST=127.0.0.1
 *   SIMCTL_CHILD_PROXY_PORT=8080
 *   xcrun simctl launch <udid> <bundle_id>
 */

#import <Foundation/Foundation.h>
#import <objc/runtime.h>

static NSString *proxyHost = nil;
static NSInteger proxyPort = 0;

static NSDictionary *buildProxyDict(void) {
    if (!proxyHost || proxyPort == 0) return nil;
    return @{
        // HTTP proxy
        (NSString *)kCFNetworkProxiesHTTPEnable: @YES,
        (NSString *)kCFNetworkProxiesHTTPProxy: proxyHost,
        (NSString *)kCFNetworkProxiesHTTPPort: @(proxyPort),
        // HTTPS proxy
        @"HTTPSEnable": @YES,
        @"HTTPSProxy": proxyHost,
        @"HTTPSPort": @(proxyPort),
    };
}

// Swizzled getter for connectionProxyDictionary
static NSDictionary * _Nullable swizzled_connectionProxyDictionary(id self, SEL _cmd) {
    return buildProxyDict();
}

__attribute__((constructor))
static void proxy_inject_init(void) {
    @autoreleasepool {
        proxyHost = NSProcessInfo.processInfo.environment[@"PROXY_HOST"] ?: @"127.0.0.1";
        NSString *portStr = NSProcessInfo.processInfo.environment[@"PROXY_PORT"] ?: @"8080";
        proxyPort = portStr.integerValue;

        if (proxyPort == 0) {
            NSLog(@"[ProxyInject] PROXY_PORT not set or invalid, skipping.");
            return;
        }

        NSLog(@"[ProxyInject] Injecting proxy %@:%ld", proxyHost, (long)proxyPort);

        // Swizzle -[NSURLSessionConfiguration connectionProxyDictionary]
        // to always return our proxy config
        Class cls = [NSURLSessionConfiguration class];

        // Replace the getter of connectionProxyDictionary property
        Method original = class_getInstanceMethod(cls, @selector(connectionProxyDictionary));
        if (original) {
            method_setImplementation(original, (IMP)swizzled_connectionProxyDictionary);
            NSLog(@"[ProxyInject] Swizzled connectionProxyDictionary getter.");
        }

        // Also set the default/ephemeral configurations' proxy at creation time
        // by swizzling the class methods
        SEL defaultSel = @selector(defaultSessionConfiguration);
        Method defaultMethod = class_getClassMethod(cls, defaultSel);
        if (defaultMethod) {
            IMP origIMP = method_getImplementation(defaultMethod);
            IMP newIMP = imp_implementationWithBlock(^NSURLSessionConfiguration *(id _self) {
                NSURLSessionConfiguration *config = ((NSURLSessionConfiguration *(*)(id, SEL))origIMP)(_self, defaultSel);
                config.connectionProxyDictionary = buildProxyDict();
                return config;
            });
            method_setImplementation(defaultMethod, newIMP);
            NSLog(@"[ProxyInject] Swizzled defaultSessionConfiguration.");
        }

        SEL ephemeralSel = @selector(ephemeralSessionConfiguration);
        Method ephemeralMethod = class_getClassMethod(cls, ephemeralSel);
        if (ephemeralMethod) {
            IMP origIMP = method_getImplementation(ephemeralMethod);
            IMP newIMP = imp_implementationWithBlock(^NSURLSessionConfiguration *(id _self) {
                NSURLSessionConfiguration *config = ((NSURLSessionConfiguration *(*)(id, SEL))origIMP)(_self, ephemeralSel);
                config.connectionProxyDictionary = buildProxyDict();
                return config;
            });
            method_setImplementation(ephemeralMethod, newIMP);
            NSLog(@"[ProxyInject] Swizzled ephemeralSessionConfiguration.");
        }

        NSLog(@"[ProxyInject] Ready. All NSURLSession traffic -> %@:%ld", proxyHost, (long)proxyPort);
    }
}
