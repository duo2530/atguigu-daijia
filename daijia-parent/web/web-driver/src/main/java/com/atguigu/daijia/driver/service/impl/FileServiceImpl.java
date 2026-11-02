package com.atguigu.daijia.driver.service.impl;

import com.atguigu.daijia.driver.service.CosService;
import com.atguigu.daijia.driver.service.FileService;
import com.atguigu.daijia.model.vo.driver.CosUploadVo;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

@Slf4j
@Service
@SuppressWarnings({"unchecked", "rawtypes"})
public class FileServiceImpl implements FileService {

    @Autowired
    private CosService cosService;

    //上传到腾讯云 COS(原 MinIO),返回存储路径(如 /driver/car/xxx.jpg)。
    //不返回临时访问地址:带签名的地址很长,超过 order_info.car_front_url 等字段的 200 字符,入库会失败
    @Override
    public String upload(MultipartFile file) {
        CosUploadVo cosUploadVo = cosService.uploadFile(file, "car");
        return cosUploadVo.getUrl();
    }
}
